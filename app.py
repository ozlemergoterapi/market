"""Market cari takip — Python 3.11+, SQLite, bağımlılıksız yerel sunucu."""
import hashlib
import base64
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from invoice_parser import parse_invoice, check_items, merge_readings
from invoice_ocr import parse_tsv_table
from product_matching import compare_items, identity as product_identity

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('MARKET_DB', str(ROOT / 'data/market.sqlite3')))


def backup_directory():
    configured = os.environ.get('MARKET_BACKUP_DIR')
    return Path(configured) if configured else DB.parent / 'backups'


def automatic_backup(now=None, keep=30):
    """Create a consistent startup snapshot and retain the newest snapshots."""
    if not DB.exists() or not DB.stat().st_size:
        return None
    now = now or datetime.now(ZoneInfo('Europe/Istanbul'))
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('market-' + now.strftime('%Y-%m-%d_%H-%M-%S') + '.sqlite3')
    if target.exists():
        return target
    temporary = target.with_suffix('.tmp')
    try:
        with sqlite3.connect(DB) as source, sqlite3.connect(temporary) as destination:
            if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('Veritabanı bütünlük kontrolünden geçemedi.')
            source.backup(destination)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    backups = sorted(directory.glob('market-*.sqlite3'), key=lambda path: path.name, reverse=True)
    for old in backups[max(1, int(keep)):]:
        old.unlink()
    return target


def backup_status():
    directory = backup_directory()
    backups = sorted(directory.glob('market-*.sqlite3'), key=lambda path: path.name, reverse=True) if directory.exists() else []
    return {
        'directory': str(directory),
        'latest': backups[0].name if backups else '',
        'count': len(backups),
    }


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS product_matches(fingerprint TEXT PRIMARY KEY,left_key TEXT NOT NULL,right_key TEXT NOT NULL,decision TEXT NOT NULL,decided_on TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS price_reviews(fingerprint TEXT PRIMARY KEY, reviewed_on TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS edits(id INTEGER PRIMARY KEY, entity TEXT NOT NULL, record_id INTEGER NOT NULL, changed_on TEXT NOT NULL, before_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS firms(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
          number TEXT NOT NULL, issued TEXT NOT NULL, due TEXT NOT NULL, total INTEGER NOT NULL CHECK(total>0),
          photo TEXT, UNIQUE(firm_id,number));
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
          product TEXT NOT NULL, unit TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>0));
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
          amount INTEGER NOT NULL CHECK(amount>0), paid_on TEXT NOT NULL);
        ''')
        if 'identity' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN identity TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE invoices SET identity=lower(hex(randomblob(16))) WHERE identity=''")
        if 'import_meta' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN import_meta TEXT NOT NULL DEFAULT '{}'")
        if 'extra_photos' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN extra_photos TEXT NOT NULL DEFAULT '[]'")
        if 'unit_price' not in {r[1] for r in db.execute('PRAGMA table_info(items)')}:
            db.execute("ALTER TABLE items ADD COLUMN unit_price TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE items SET unit_price=printf('%.4f',price/100.0) WHERE unit_price=''")
        item_columns = {r[1] for r in db.execute('PRAGMA table_info(items)')}
        for column in ('quantity','code','source_net','source_vat','row_number','line_total'):
            if column not in item_columns:
                db.execute(f"ALTER TABLE items ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def money(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n <= 0 or n > 999999999:
            raise ValueError('Tutar sıfırdan büyük olmalı.')
        if n != n.quantize(Decimal('.01')):
            raise ValueError('Tutar en fazla iki ondalık basamak içerebilir.')
        return int(n * 100)
    except InvalidOperation:
        raise ValueError('Geçerli bir tutar girin.')


def clean(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f'{label} alanını doldurun (en fazla 200 karakter).')
    return value.strip()


def photo(value):
    if not value:
        return None
    if not isinstance(value, str) or not re.match(r'^data:image/(jpeg|png|webp);base64,', value):
        raise ValueError('JPEG, PNG veya WebP fotoğraf seçin.')
    raw = base64.b64decode(value.split(',', 1)[1], validate=True)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Fotoğraf en fazla 8 MB olabilir.')
    return value


def tsv_reading(tsv_text):
    """Adapt coordinate-based OCR rows to the normal invoice reading shape."""
    table = parse_tsv_table(tsv_text)
    return dict(
        number='', issued='', total='', supplier='', firm_id=None,
        items=table['items'], warnings=[], table_warnings=table['warnings'],
        sources={}, import_check={'expected_rows': table['expected_rows']},
    )


def run_tesseract(pic, lang, mode, with_tsv=True):
    """Read one prepared document region, optionally retaining word positions."""
    match = re.match(r'^data:image/(jpeg|png|webp);base64,', pic)
    if not match:
        raise ValueError('Fotoğraf verisi geçersiz.')
    suffix = {'jpeg': '.jpg', 'png': '.png', 'webp': '.webp'}[match.group(1)]
    raw = base64.b64decode(pic.split(',', 1)[1], validate=True)
    with tempfile.TemporaryDirectory(prefix='sancakli-ocr-') as directory:
        source = Path(directory) / ('page' + suffix)
        output = Path(directory) / 'reading'
        source.write_bytes(raw)
        command = [
            'tesseract', str(source), str(output), '-l', lang,
            '--psm', str(mode), '--dpi', '300',
            '-c', 'preserve_interword_spaces=1', 'txt',
        ]
        if with_tsv:
            command.append('tsv')
        read = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if read.returncode:
            raise ValueError('Bir fotoğraf okunamadı. Daha net JPEG veya PNG deneyin.')
        text_path = output.with_suffix('.txt')
        tsv_path = output.with_suffix('.tsv')
        text = text_path.read_text(encoding='utf-8', errors='replace') if text_path.exists() else ''
        tsv = tsv_path.read_text(encoding='utf-8', errors='replace') if with_tsv and tsv_path.exists() else ''
        return text, tsv


def state(today=None):
    today = today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    with connect() as db:
        firms = [dict(r) for r in db.execute('SELECT * FROM firms ORDER BY name')]
        invoices = [dict(r) for r in db.execute('''SELECT i.id,i.firm_id,f.name AS firm,i.number,i.issued,i.due,i.total,i.import_meta,
          COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) AS paid,
          photo IS NOT NULL AS has_photo FROM invoices i JOIN firms f ON f.id=i.firm_id ORDER BY due,i.id''')]
        payments = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY paid_on DESC,id DESC')]
        product_decisions=[dict(r) for r in db.execute('SELECT * FROM product_matches')]
        reviews = {r['fingerprint'] for r in db.execute('SELECT fingerprint FROM price_reviews')}
        items = [dict(r) for r in db.execute('''SELECT t.*,i.firm_id,i.issued,i.number,i.identity,f.name AS firm FROM items t
          JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id ORDER BY i.issued,i.id,t.id''')]
    for item in items:
        item['price'] = float(Decimal(item['unit_price']) * 100)
    totals = dict(today=0, overdue=0, upcoming=0, total=0)
    for i in invoices:
        i['remaining'] = i['total'] - i['paid']
        due = date.fromisoformat(i['due'])
        i['late_days'] = max(0, (today - due).days) if i['remaining'] else 0
        totals['total'] += i['remaining']
        if due == today:
            totals['today'] += i['remaining']
        if due < today:
            totals['overdue'] += i['remaining']
        if today < due <= today + timedelta(days=7):
            totals['upcoming'] += i['remaining']
    comparisons,suggestions,unmatched=compare_items(items,product_decisions)
    names={product_identity(item):item['product'] for item in items}
    match_history=[dict(review,left_name=names.get(review['left_key'],json.loads(review['left_key'])[1]),right_name=names.get(review['right_key'],json.loads(review['right_key'])[1])) for review in product_decisions]
    alerts=[]
    price_history_groups={}
    for comparison in comparisons:
        item,old=comparison['current'],comparison['previous']
        history_key=comparison['identity']
        history_id=hashlib.sha256(history_key.encode()).hexdigest()[:20]
        group=price_history_groups.setdefault(history_key,dict(id=history_id,firm=item['firm'],unit=item['unit'],code=item.get('code') or old.get('code') or '',product=item['product'],points={}))
        group['product']=item['product'];group['code']=item.get('code') or group['code']
        for point in (old,item):
            group['points'][point['id']]=dict(item_id=point['id'],invoice_id=point['invoice_id'],issued=point['issued'],number=point['number'],product=point['product'],price=point['price'])
        if old['price']!=item['price']:
            fingerprint=hashlib.sha256(json.dumps([comparison['identity'],old['identity'],old['invoice_id'],old['issued'],old['price'],item['identity'],item['invoice_id'],item['issued'],item['price']],ensure_ascii=False).encode()).hexdigest()
            alerts.append(dict(item,fingerprint=fingerprint,history_id=history_id,approved=fingerprint in reviews,old_product=old['product'],matched_by=comparison['matched_by'],old_price=old['price'],change=round((item['price']/old['price']-1)*100,2)))
    price_histories=[]
    for group in price_history_groups.values():
        group['points']=sorted(group['points'].values(),key=lambda point:(point['issued'],point['invoice_id'],point['item_id']))
        if len(group['points'])>1:
            price_histories.append(group)
    price_histories.sort(key=lambda group:(group['firm'],group['product']))
    for f in firms:
        f['balance'] = sum(i['remaining'] for i in invoices if i['firm_id'] == f['id'])
    return dict(today=today.isoformat(), totals=totals, firms=firms, invoices=invoices, payments=payments, items=items, alerts=alerts[::-1],price_histories=price_histories,product_suggestions=suggestions,unmatched_products=unmatched,product_match_history=match_history,backup=backup_status())


def audit(db, entity, record_id, before):
    db.execute("INSERT INTO edits(entity,record_id,changed_on,before_json) VALUES(?,?,?,?)", (entity,record_id,datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(),json.dumps(before,ensure_ascii=False)))


def delete_record(entity, record_id, expected=None):
    tables = {'firm': 'firms', 'invoice': 'invoices', 'payment': 'payments'}
    if entity not in tables:
        raise ValueError('Geçersiz kayıt türü.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute(f'SELECT * FROM {tables[entity]} WHERE id=?', (record_id,)).fetchone()
        if not old:
            raise ValueError('Kayıt bulunamadı. Listeyi yenileyin.')
        invoices = []
        if entity == 'firm':
            invoices = [dict(r) for r in db.execute('SELECT * FROM invoices WHERE firm_id=?',(record_id,))]
        elif entity == 'invoice':
            invoices = [dict(old)]
        payments, items = [], []
        for invoice in invoices:
            payments.extend(dict(r) for r in db.execute('SELECT * FROM payments WHERE invoice_id=?',(invoice['id'],)))
            items.extend(dict(r) for r in db.execute('SELECT * FROM items WHERE invoice_id=?',(invoice['id'],)))
        counts = {'invoices':len(invoices),'payments':len(payments),'items':len(items)}
        if entity != 'payment' and expected != counts:
            raise ValueError('Bağlı kayıtlar değişmiş veya silme onayı eksik. Listeyi yenileyip silme işlemini tekrar onaylayın.')
        audit(db,entity+'_deleted',record_id,dict(record=dict(old),invoices=invoices,payments=payments,items=items))
        for invoice in invoices:
            db.execute('DELETE FROM payments WHERE invoice_id=?',(invoice['id'],))
            db.execute('DELETE FROM items WHERE invoice_id=?',(invoice['id'],))
            db.execute('DELETE FROM invoices WHERE id=?',(invoice['id'],))
        if entity=='firm':
            for review in db.execute('SELECT fingerprint,left_key FROM product_matches').fetchall():
                if json.loads(review['left_key'])[0]==record_id:
                    db.execute('DELETE FROM product_matches WHERE fingerprint=?',(review['fingerprint'],))
        if entity != 'invoice':
            db.execute(f'DELETE FROM {tables[entity]} WHERE id=?',(record_id,))



def delete_firm(record_id):
    # Compatibility helper for callers deleting an empty firm.
    delete_record('firm',record_id,dict(invoices=0,payments=0,items=0))


def create_invoice(data, record_id=None):
    issued = date.fromisoformat(data['issued'])
    days = int(data['days'])
    if days < 0 or days > 3650:
        raise ValueError('Vade 0 ile 3650 gün arasında olmalı.')
    total = money(data['total'])
    number = clean(data['number'], 'Fatura numarası')
    photos = data.get('photos') or ([data['photo']] if data.get('photo') else [])
    if not isinstance(photos,list) or len(photos)>6:
        raise ValueError('En fazla 6 fotoğraf seçin.')
    photos = [photo(p) for p in photos]
    pic = photos[0] if photos else None
    items = data.get('items', [])
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError('Ürün listesi geçersiz.')
    import_metadata = data.get('import_check')
    if import_metadata is not None:
        checked=check_items(items,str(Decimal(total)/100),import_metadata)
        if checked['errors']:
            raise ValueError('Kalemler eksik veya tutarsız: '+' '.join(checked['errors']))
    rows = []
    for t in items:
        try:
            value = Decimal(str(t['price']))
            if not value.is_finite() or value <= 0 or value > 999999999 or value != value.quantize(Decimal('.0001')):
                raise ValueError('Birim fiyat sıfırdan büyük ve en fazla dört ondalık basamaklı olmalı.')
        except InvalidOperation:
            raise ValueError('Birim fiyat geçersiz.')
        cents = max(1, int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
        quantity = ''
        if 'quantity' in t:
            try:
                quantity_value = Decimal(str(t['quantity']))
                if not quantity_value.is_finite() or quantity_value <= 0 or quantity_value > 99999999 or quantity_value != quantity_value.quantize(Decimal('.0001')):
                    raise ValueError('Ürün miktarı sıfırdan büyük ve en fazla dört ondalıklı olmalı.')
                quantity = str(quantity_value)
            except InvalidOperation:
                raise ValueError('Ürün miktarını kontrol edin.')
        source = []
        for field in ('net','vat'):
            raw = t.get(field)
            if raw is None or raw == '':
                source.append('')
            else:
                try:
                    number_value = Decimal(str(raw))
                    if not number_value.is_finite() or number_value < 0 or number_value > 999999999:
                        raise ValueError('Kaynak satır tutarı geçersiz.')
                    source.append(str(number_value))
                except InvalidOperation:
                    raise ValueError('Kaynak satır tutarı geçersiz.')
        code = str(t.get('code') or '').strip()
        if len(code)>100:
            raise ValueError('Ürün kodu en fazla 100 karakter olabilir.')
        row_number=str(t.get('row_number') or '')
        line_total=str(t.get('line_total') or '')
        if len(row_number)>5 or len(line_total)>30:
            raise ValueError('Satır bilgileri geçersiz.')
        rows.append((clean(t['product'], 'Ürün'), clean(t['unit'], 'Ambalaj / fiyat birimi'), cents, str(value), quantity, code, *source,row_number,line_total))
    if len({(p.casefold(), u.casefold()) for p,u,*_ in rows}) != len(rows):
        raise ValueError('Aynı ürün ve birimi faturada bir kez girin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        values = (int(data['firm_id']), number, issued.isoformat(), (issued + timedelta(days=days)).isoformat(), total, pic)
        if record_id is None:
            cur = db.execute('INSERT INTO invoices(firm_id,number,issued,due,total,photo) VALUES(?,?,?,?,?,?)', values)
            record_id = cur.lastrowid
            db.execute("UPDATE invoices SET identity=lower(hex(randomblob(16))) WHERE id=?",(record_id,))
        else:
            old = db.execute('SELECT * FROM invoices WHERE id=?',(record_id,)).fetchone()
            if not old:
                raise ValueError('Fatura bulunamadı.')
            saved_metadata=json.loads(old['import_meta'])
            if saved_metadata.get('expected_rows') and not import_metadata:
                checked=check_items(items,str(Decimal(total)/100),saved_metadata)
                if checked['errors']:
                    raise ValueError('Kalemler eksik veya tutarsız: '+' '.join(checked['errors']))
                import_metadata=saved_metadata
            paid = db.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?',(record_id,)).fetchone()[0]
            if total < paid:
                raise ValueError('Fatura toplamı yapılan ödemelerden az olamaz. Önce yanlış ödemeyi düzeltin.')
            before = dict(old)
            before['items'] = [dict(r) for r in db.execute('SELECT * FROM items WHERE invoice_id=?',(record_id,))]
            audit(db,'invoice',record_id,before)
            db.execute('UPDATE invoices SET firm_id=?,number=?,issued=?,due=?,total=?,photo=? WHERE id=?', (*values,record_id))
            db.execute('DELETE FROM items WHERE invoice_id=?',(record_id,))
        db.execute('UPDATE invoices SET extra_photos=?,import_meta=? WHERE id=?',(json.dumps(photos[1:]),json.dumps(import_metadata or {}),record_id))
        db.executemany('INSERT INTO items(invoice_id,product,unit,price,unit_price,quantity,code,source_net,source_vat,row_number,line_total) VALUES(?,?,?,?,?,?,?,?,?,?,?)', [(record_id,*r) for r in rows])


def create_payment(data, record_id=None):
    amount = money(data['amount'])
    paid_on = date.fromisoformat(data['paid_on'])
    if paid_on > datetime.now(ZoneInfo('Europe/Istanbul')).date():
        raise ValueError('Ödeme tarihi gelecekte olamaz.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = None
        if record_id is not None:
            old = db.execute('SELECT * FROM payments WHERE id=?',(record_id,)).fetchone()
            if not old:
                raise ValueError('Ödeme bulunamadı.')
        row = db.execute('SELECT total-COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id AND id!=?),0) AS remaining FROM invoices i WHERE id=?', (record_id or -1,int(data['invoice_id']))).fetchone()
        if not row or amount > row['remaining']:
            raise ValueError('Ödeme kalan borcu aşamaz.')
        values = (int(data['invoice_id']), amount, paid_on.isoformat())
        if record_id is None:
            db.execute('INSERT INTO payments(invoice_id,amount,paid_on) VALUES(?,?,?)', values)
        else:
            audit(db,'payment',record_id,dict(old))
            db.execute('UPDATE payments SET invoice_id=?,amount=?,paid_on=? WHERE id=?',(*values,record_id))


class Handler(BaseHTTPRequestHandler):
    def respond(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.path = self.path.split("?", 1)[0]
        if self.path == '/api/state':
            return self.respond(state())
        if self.path.startswith('/api/photo/'):
            try:
                with connect() as db:
                    row = db.execute('SELECT photo,extra_photos FROM invoices WHERE id=?', (int(self.path.rsplit('/',1)[1]),)).fetchone()
                return self.respond({'photo': row['photo'] if row else None,'photos':([row['photo']]+json.loads(row['extra_photos'])) if row and row['photo'] else []})
            except ValueError:
                return self.respond({'error': 'Geçersiz kayıt.'}, 400)
        files = {
            '/': ('index.html','text/html'),
            '/app.js': ('app.js','text/javascript'),
            '/style.css': ('style.css','text/css'),
            '/logo.png': ('logo.png','image/png'),
            '/vendor/pdfjs/pdf.min.mjs': ('vendor/pdfjs/pdf.min.mjs','text/javascript'),
            '/vendor/pdfjs/pdf.worker.min.mjs': ('vendor/pdfjs/pdf.worker.min.mjs','text/javascript'),
            '/vendor/pdfjs/LICENSE': ('vendor/pdfjs/LICENSE','text/plain'),
        }
        if self.path not in files:
            return self.respond({'error':'Bulunamadı.'},404)
        filename, kind = files[self.path]
        self.send_response(200)
        self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write((ROOT / 'static' / filename).read_bytes())

    def do_POST(self):
        # Local single-user app: reject cross-site writes, never enable public binding by default.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.respond({'error':'Kaynak doğrulanamadı.'},403)
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 12 * 1024 * 1024:
                raise ValueError('İstek boyutu geçersiz (en fazla 12 MB).')
            data = json.loads(self.rfile.read(size))
            if self.path == '/api/firms':
                with connect() as db:
                    db.execute('INSERT INTO firms(name) VALUES(?)', (clean(data['name'],'Firma adı'),))
            elif self.path == '/api/invoices':
                create_invoice(data)
            elif self.path == '/api/payments':
                create_payment(data)
            elif self.path == '/api/invoices/edit':
                create_invoice(data, int(data['id']))
            elif self.path == '/api/payments/edit':
                create_payment(data, int(data['id']))
            elif self.path == '/api/firms/delete':
                delete_record('firm',int(data['id']),data.get('expected'))
            elif self.path == '/api/invoices/delete':
                delete_record('invoice',int(data['id']),data.get('expected'))
            elif self.path == '/api/payments/delete':
                delete_record('payment',int(data['id']))
            elif self.path == '/api/firms/edit':
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    old = db.execute('SELECT * FROM firms WHERE id=?',(int(data['id']),)).fetchone()
                    if not old:
                        raise ValueError('Firma bulunamadı.')
                    audit(db,'firm',int(data['id']),dict(old))
                    db.execute('UPDATE firms SET name=? WHERE id=?',(clean(data['name'],'Firma adı'),int(data['id'])))
            elif self.path == '/api/products/unmatch':
                with connect() as db:
                    db.execute('DELETE FROM product_matches WHERE fingerprint=?',(data['fingerprint'],))
            elif self.path == '/api/products/match':
                suggestion=next((s for s in state()['product_suggestions'] if s['fingerprint']==data['fingerprint']),None)
                if not suggestion or data.get('decision') not in ('same','different'):
                    raise ValueError('Eşleştirme güncellenmiş veya karar geçersiz. Listeyi yenileyin.')
                with connect() as db:
                    db.execute('INSERT OR REPLACE INTO product_matches VALUES(?,?,?,?,?)',(suggestion['fingerprint'],suggestion['left_key'],suggestion['right_key'],data['decision'],datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()))
            elif self.path == '/api/prices/approve':
                fingerprint = data['fingerprint']
                if fingerprint not in {a['fingerprint'] for a in state()['alerts']}:
                    raise ValueError('Fiyat değişimi güncellenmiş. Listeyi yenileyip tekrar kontrol edin.')
                with connect() as db:
                    db.execute('INSERT OR IGNORE INTO price_reviews VALUES(?,?)',(fingerprint,datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()))
            elif self.path == '/api/ocr':
                photos = data.get('photos') or ([data['photo']] if data.get('photo') else [])
                if not isinstance(photos,list) or not 1 <= len(photos) <= 6:
                    raise ValueError('1 ile 6 arasında fotoğraf seçin.')
                photos = [photo(p) for p in photos]
                if not all(photos):
                    raise ValueError('Fotoğraf verisi geçersiz.')
                languages = subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,check=True).stdout
                lang = 'tur+eng' if 'tur' in languages.split() else 'eng'
                with connect() as db:
                    firms = [dict(r) for r in db.execute('SELECT id,name FROM firms')]
                readings, visible_text = [], []

                def add_layout(mode, with_tsv=True):
                    for index, pic in enumerate(photos, 1):
                        page_text, page_tsv = run_tesseract(pic, lang, mode, with_tsv)
                        visible_text.append(f'--- Görsel {index} · OCR düzeni {mode} ---\n{page_text}')
                        plain = parse_invoice(page_text, firms)
                        if page_tsv:
                            positioned = tsv_reading(page_tsv)
                            positioned_rows = {item['row_number'] for item in positioned['items']}
                            # Keep plain-text rows only when the coordinate reading
                            # did not already reconstruct that invoice row.
                            plain['items'] = [
                                item for item in plain['items']
                                if item['row_number'] not in positioned_rows
                            ]
                            readings.append(positioned)
                        readings.append(plain)

                # PSM 4 keeps ruled invoice tables as columns; TSV coordinates put
                # the cells back into rows. Sparse text is a conservative fallback.
                add_layout(4)
                draft = merge_readings(readings)
                table_errors = [
                    error for error in draft['coverage']['errors']
                    if error != 'Fatura toplamı okunamadı veya eksik.'
                ]
                if not draft['items'] or table_errors:
                    add_layout(11)
                    draft = merge_readings(readings)
                if not draft['items']:
                    add_layout(6)
                    draft = merge_readings(readings)
                if not all(draft[key] for key in ('number', 'issued', 'total')):
                    for index, pic in enumerate(photos, 1):
                        page_text, _ = run_tesseract(pic, lang, 3, False)
                        visible_text.append(f'--- Görsel {index} · üst bilgi okuması ---\n{page_text}')
                        readings.append(parse_invoice(page_text, firms))
                        draft = merge_readings(readings)
                        if all(draft[key] for key in ('number', 'issued', 'total')):
                            break
                return self.respond({'text':'\n'.join(visible_text),'language':lang,'draft':draft})
            else:
                return self.respond({'error':'Bulunamadı.'},404)
            self.respond({'ok':True},201)
        except sqlite3.IntegrityError:
            self.respond({'error':'Firma veya fatura zaten kayıtlı ya da firma seçimi geçersiz.'},400)
        except ValueError as error:
            self.respond({'error': str(error) if str(error) else 'Bilgileri kontrol edin.'},400)
        except (KeyError, TypeError, OverflowError):
            self.respond({'error':'Bilgileri kontrol edin. Tutar, tarih ve zorunlu alanlar geçerli olmalı.'},400)
        except (FileNotFoundError, subprocess.SubprocessError):
            self.respond({'error':'Fotoğraf okuma motoru kullanılamıyor. Bilgileri elle girebilirsiniz.'},503)


if __name__ == '__main__':
    try:
        created_backup = automatic_backup()
        if created_backup:
            print('Otomatik yedek oluşturuldu: ' + str(created_backup), flush=True)
    except (OSError, sqlite3.Error) as error:
        print('Otomatik yedek oluşturulamadı: ' + str(error), flush=True)
    init()
    print('Market Cari geliştirme sunucusu: 127.0.0.1:8000',flush=True)
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
