"""Market cari takip — Python 3.11+, SQLite, bağımlılıksız yerel sunucu."""
import hashlib
import base64
import io
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo
from invoice_parser import parse_invoice, check_items, merge_readings
from invoice_ocr import parse_tsv_table
from product_matching import compare_items, identity as product_identity

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('MARKET_DB', str(ROOT / 'data/market.sqlite3')))


def settings_path():
    return DB.parent / 'settings.json'


def settings():
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def backup_directory():
    configured = os.environ.get('MARKET_BACKUP_DIR')
    saved = settings().get('backup_directory')
    return Path(configured or saved) if configured or saved else DB.parent / 'backups'


def set_backup_directory(path=None):
    """Persist a writable backup directory, or reset to the app default."""
    value = settings()
    if path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            raise ValueError('Yedek klasörü tam bir klasör yolu olmalı.')
        target.mkdir(parents=True, exist_ok=True)
        probe = target / '.sancakli-write-test'
        try:
            probe.write_text('ok', encoding='utf-8')
        finally:
            if probe.exists():
                probe.unlink()
        value['backup_directory'] = str(target.resolve())
    else:
        value.pop('backup_directory', None)
    target_settings = settings_path()
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_settings.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, target_settings)
    return backup_directory()


def choose_backup_directory():
    """Open the operating system's folder picker on the local computer."""
    if sys.platform == 'darwin':
        command = ['osascript', '-e', 'POSIX path of (choose folder with prompt "Sancaklı Market yedek klasörünü seçin")']
    elif sys.platform == 'win32':
        script = ('$d=New-Object System.Windows.Forms.FolderBrowserDialog;'
                  '$d.Description="Sancaklı Market yedek klasörünü seçin";'
                  'if($d.ShowDialog() -eq "OK"){$d.SelectedPath}else{exit 1}')
        command = ['powershell', '-NoProfile', '-STA', '-Command',
                   'Add-Type -AssemblyName System.Windows.Forms;' + script]
    else:
        command = ['zenity', '--file-selection', '--directory', '--title=Sancaklı Market yedek klasörünü seçin']
    try:
        selected = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise ValueError('Bu bilgisayarda klasör seçme penceresi açılamadı.')
    if selected.returncode:
        return None
    path = selected.stdout.strip()
    return set_backup_directory(path) if path else None


def automatic_backup(now=None, keep=30):
    """Create a consistent startup snapshot and retain the newest snapshots."""
    if not DB.exists() or not DB.stat().st_size:
        return None
    now = now or datetime.now(ZoneInfo('Europe/Istanbul'))
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('market-' + now.strftime('%Y-%m-%d_%H-%M-%S-%f') + '.sqlite3')
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
        'files': [{'name': path.name, 'size': path.stat().st_size} for path in backups],
    }


def restore_backup(name):
    """Validate and atomically restore a selected application backup."""
    if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r'market-[0-9_-]+\.sqlite3', name):
        raise ValueError('Geçerli bir yedek seçin.')
    source_path = backup_directory() / name
    if not source_path.is_file():
        raise ValueError('Yedek dosyası bulunamadı.')
    with sqlite3.connect(source_path) as source:
        if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Seçilen yedek bütünlük kontrolünden geçemedi.')
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'firms','invoices','items','payments'} <= tables:
            raise ValueError('Seçilen dosya Sancaklı Market yedeği değil.')
    temporary = DB.with_suffix('.restore.tmp')
    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        automatic_backup()
        os.replace(temporary, DB)
        init()
    finally:
        if temporary.exists():
            temporary.unlink()


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
        CREATE TABLE IF NOT EXISTS account_entries(id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
          invoice_id INTEGER REFERENCES invoices(id), kind TEXT NOT NULL CHECK(kind IN ('opening','credit')),
          entry_date TEXT NOT NULL, due TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), reference TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS opening_payments(id INTEGER PRIMARY KEY, opening_id INTEGER NOT NULL REFERENCES account_entries(id),
          amount INTEGER NOT NULL CHECK(amount>0), paid_on TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS firm_ocr_profiles(firm_id INTEGER NOT NULL REFERENCES firms(id),supplier_alias TEXT NOT NULL,
          sample_count INTEGER NOT NULL DEFAULT 1,last_seen TEXT NOT NULL,PRIMARY KEY(firm_id,supplier_alias));
        CREATE TABLE IF NOT EXISTS form_drafts(draft_key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_on TEXT NOT NULL);
        ''')
        if 'identity' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN identity TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE invoices SET identity=lower(hex(randomblob(16))) WHERE identity=''")
        if 'import_meta' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN import_meta TEXT NOT NULL DEFAULT '{}'")
        if 'extra_photos' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN extra_photos TEXT NOT NULL DEFAULT '[]'")
        if 'source_hash' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
            db.execute("ALTER TABLE invoices ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''")
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


def clean(value, label, max_length=200):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f'{label} alanını doldurun (en fazla {max_length} karakter).')
    return value.strip()


def draft_key(value):
    value = str(value or '')
    if not re.fullmatch(r'(firm|invoice|payment|entry):(new|[1-9][0-9]*)', value):
        raise ValueError('Taslak türü geçersiz.')
    return value


def save_draft(key, payload):
    key = draft_key(key)
    if not isinstance(payload, dict):
        raise ValueError('Taslak içeriği geçersiz.')
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(serialized.encode('utf-8')) > 11 * 1024 * 1024:
        raise ValueError('Taslak en fazla 11 MB olabilir.')
    with connect() as db:
        db.execute('''INSERT INTO form_drafts(draft_key,payload,updated_on) VALUES(?,?,?)
          ON CONFLICT(draft_key) DO UPDATE SET payload=excluded.payload,updated_on=excluded.updated_on''',
          (key, serialized, datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()))


def load_draft(key):
    key = draft_key(key)
    with connect() as db:
        row = db.execute('SELECT payload,updated_on FROM form_drafts WHERE draft_key=?', (key,)).fetchone()
    return dict(payload=json.loads(row['payload']), updated_on=row['updated_on']) if row else None


def clear_draft(key):
    key = draft_key(key)
    with connect() as db:
        db.execute('DELETE FROM form_drafts WHERE draft_key=?', (key,))


def photo(value):
    if not value:
        return None
    if not isinstance(value, str) or not re.match(r'^data:image/(jpeg|png|webp|heic|heif);base64,', value):
        raise ValueError('HEIC, JPEG, PNG veya WebP fotoğraf seçin.')
    raw = base64.b64decode(value.split(',', 1)[1], validate=True)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Fotoğraf en fazla 8 MB olabilir.')
    if re.match(r'^data:image/(heic|heif);', value):
        try:
            from PIL import Image, ImageOps
            from pillow_heif import register_heif_opener
            register_heif_opener()
            with Image.open(io.BytesIO(raw)) as source:
                converted = ImageOps.exif_transpose(source).convert('RGB')
                converted.thumbnail((3600, 5200), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                converted.save(output, 'JPEG', quality=92, optimize=True)
            raw = output.getvalue()
            value = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
        except (ImportError, OSError, ValueError) as error:
            raise ValueError(
                'HEIC fotoğraf dönüştürülemedi. Güncel kurulum dosyasını yeniden '
                'çalıştırın veya fotoğrafı JPEG olarak ekleyin.'
            ) from error
    return value


def photos_hash(photos):
    if not photos:
        return ''
    digest = hashlib.sha256()
    for value in photos:
        digest.update(base64.b64decode(value.split(',', 1)[1], validate=True))
        digest.update(b'\0PAGE\0')
    return digest.hexdigest()


def tsv_reading(tsv_text):
    """Adapt coordinate-based OCR rows to the normal invoice reading shape."""
    table = parse_tsv_table(tsv_text)
    return dict(
        number='', issued='', total='', supplier='', firm_id=None,
        items=table['items'], warnings=[], table_warnings=table['warnings'],
        sources={}, import_check={'expected_rows': table['expected_rows'],'detected_rows':table.get('detected_rows',[]),'line_total_kind':'net'},
    )


def pdf_text_readings(layers, firms):
    """Convert PDF.js text coordinates into exact parser readings."""
    if not isinstance(layers, list) or len(layers) > 6:
        raise ValueError('PDF metin katmanı geçersiz.')
    tsv = ['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    visible_pages = []
    word_number = 0
    for page_number, layer in enumerate(layers, 1):
        if not isinstance(layer, dict) or not isinstance(layer.get('words'), list) or len(layer['words']) > 10000:
            raise ValueError('PDF metin katmanı geçersiz.')
        words = []
        for raw in layer['words']:
            try:
                text = str(raw['text']).replace('\t',' ').replace('\r',' ').replace('\n',' ').strip()
                left, top = int(raw['left']), int(raw['top'])
                width, height = int(raw['width']), int(raw['height'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('PDF metin katmanı geçersiz.')
            if not text:
                continue
            if not (0 <= left <= 20000 and 0 <= top <= 30000 and 1 <= width <= 20000 and 1 <= height <= 5000):
                raise ValueError('PDF metin koordinatları geçersiz.')
            word_number += 1
            words.append(dict(text=text,left=left,top=top,width=width,height=height,center=top+height/2))
            tsv.append(f'5\t{page_number}\t1\t1\t1\t{word_number}\t{left}\t{top}\t{width}\t{height}\t100\t{text}')
        if words:
            tolerance=max(3,statistics.median(word['height'] for word in words)*.65)
            lines=[]
            for word in sorted(words,key=lambda value:(value['center'],value['left'])):
                line=next((entry for entry in reversed(lines[-4:]) if abs(entry['center']-word['center'])<=tolerance),None)
                if line is None:
                    line=dict(center=word['center'],words=[]);lines.append(line)
                line['words'].append(word)
                line['center']=sum(value['center'] for value in line['words'])/len(line['words'])
            visible_pages.append('\n'.join(' '.join(word['text'] for word in sorted(line['words'],key=lambda value:value['left'])) for line in lines))
    if not word_number:
        return [], ''
    positioned = tsv_reading('\n'.join(tsv))
    plain = parse_invoice('\n'.join(visible_pages), firms)
    positioned_rows={item['row_number'] for item in positioned['items']}
    plain['items']=[item for item in plain['items'] if item['row_number'] not in positioned_rows]
    return [positioned,plain], '\n'.join(visible_pages)


def ocr_engine():
    """Return the OCR executable and installed languages with actionable errors."""
    executable = shutil.which('tesseract')
    if not executable:
        raise ValueError(
            'Fotoğraf okuma motoru bulunamadı. Mac-Uygulamasini-Olustur.command '
            'dosyasını yeniden çalıştırın; mevcut kayıtlarınız silinmez.'
        )
    try:
        result = subprocess.run(
            [executable, '--list-langs'], capture_output=True, text=True,
            timeout=15, check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError('Fotoğraf okuma motoru yanıt vermedi. Uygulamayı kapatıp yeniden açın.') from error
    except subprocess.SubprocessError as error:
        raise ValueError(
            'Fotoğraf okuma motoru başlatılamadı. Kurulum dosyasını yeniden çalıştırın.'
        ) from error
    languages = set(result.stdout.split())
    if 'tur' not in languages:
        raise ValueError(
            'Türkçe fotoğraf okuma paketi eksik. Mac-Uygulamasini-Olustur.command '
            'dosyasını yeniden çalıştırın; mevcut kayıtlarınız silinmez.'
        )
    return executable, 'tur+eng' if 'eng' in languages else 'tur'


def run_tesseract(pic, lang, mode, with_tsv=True, executable='tesseract'):
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
            executable, str(source), str(output), '-l', lang,
            '--psm', str(mode), '--dpi', '300',
            '-c', 'preserve_interword_spaces=1', 'txt',
        ]
        if with_tsv:
            command.append('tsv')
        try:
            read = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                'Fotoğrafın okunması bir dakikayı aştı. Fotoğrafı yalnızca fatura görünecek '
                'biçimde kırpıp yeniden deneyin.'
            ) from error
        if read.returncode:
            raise ValueError(
                'Bir fotoğraf okunamadı. Fotoğrafı dik, net ve yalnızca fatura görünecek '
                'biçimde yeniden ekleyin.'
            )
        text_path = output.with_suffix('.txt')
        tsv_path = output.with_suffix('.tsv')
        text = text_path.read_text(encoding='utf-8', errors='replace') if text_path.exists() else ''
        tsv = tsv_path.read_text(encoding='utf-8', errors='replace') if with_tsv and tsv_path.exists() else ''
        return text, tsv


def state(today=None):
    today = today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    with connect() as db:
        firms = [dict(r) for r in db.execute('''SELECT f.*,
          COALESCE((SELECT SUM(sample_count) FROM firm_ocr_profiles WHERE firm_id=f.id),0) AS ocr_samples,
          COALESCE((SELECT group_concat(supplier_alias,' · ') FROM firm_ocr_profiles WHERE firm_id=f.id),'') AS ocr_aliases
          FROM firms f ORDER BY name''')]
        invoices = [dict(r) for r in db.execute('''SELECT i.id,i.firm_id,f.name AS firm,i.number,i.issued,i.due,i.total,i.import_meta,
          COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) AS paid,
          photo IS NOT NULL AS has_photo FROM invoices i JOIN firms f ON f.id=i.firm_id ORDER BY due,i.id''')]
        payments = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY paid_on DESC,id DESC')]
        entries = [dict(r) for r in db.execute('''SELECT a.*,f.name AS firm,i.number AS invoice_number,
          COALESCE((SELECT SUM(amount) FROM opening_payments WHERE opening_id=a.id),0) AS paid
          FROM account_entries a JOIN firms f ON f.id=a.firm_id LEFT JOIN invoices i ON i.id=a.invoice_id
          ORDER BY a.entry_date DESC,a.id DESC''')]
        opening_payments = [dict(r) for r in db.execute('SELECT * FROM opening_payments ORDER BY paid_on DESC,id DESC')]
        product_decisions=[dict(r) for r in db.execute('SELECT * FROM product_matches')]
        reviews = {r['fingerprint'] for r in db.execute('SELECT fingerprint FROM price_reviews')}
        items = [dict(r) for r in db.execute('''SELECT t.*,i.firm_id,i.issued,i.number,i.identity,f.name AS firm FROM items t
          JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id ORDER BY i.issued,i.id,t.id''')]
    for item in items:
        item['price'] = float(Decimal(item['unit_price']) * 100)
    totals = dict(today=0, overdue=0, upcoming=0, total=0)
    credits = [entry for entry in entries if entry['kind'] == 'credit']
    openings = [entry for entry in entries if entry['kind'] == 'opening']
    for i in invoices:
        i['credited'] = sum(entry['amount'] for entry in credits if entry['invoice_id'] == i['id'])
        i['remaining'] = i['total'] - i['paid'] - i['credited']
        due = date.fromisoformat(i['due'])
        i['late_days'] = max(0, (today - due).days) if i['remaining'] else 0
        totals['total'] += i['remaining']
        if due == today:
            totals['today'] += i['remaining']
        if due < today:
            totals['overdue'] += i['remaining']
        if today < due <= today + timedelta(days=7):
            totals['upcoming'] += i['remaining']
    for opening in openings:
        opening['remaining'] = opening['amount'] - opening['paid']
        opening['late_days'] = max(0, (today - date.fromisoformat(opening['due'])).days) if opening['remaining'] else 0
        totals['total'] += opening['remaining']
        due = date.fromisoformat(opening['due'])
        if due == today:
            totals['today'] += opening['remaining']
        if due < today:
            totals['overdue'] += opening['remaining']
        if today < due <= today + timedelta(days=7):
            totals['upcoming'] += opening['remaining']
    payments = [dict(payment, target_type='invoice', target_id=payment['invoice_id'], payment_key='invoice:'+str(payment['id'])) for payment in payments]
    payments += [dict(payment, invoice_id=None, target_type='opening', target_id=payment['opening_id'], payment_key='opening:'+str(payment['id'])) for payment in opening_payments]
    payments.sort(key=lambda payment:(payment['paid_on'],payment['id']), reverse=True)
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
        f['balance'] = sum(i['remaining'] for i in invoices if i['firm_id'] == f['id']) + sum(o['remaining'] for o in openings if o['firm_id'] == f['id'])
    obligations = [dict(invoice, kind='invoice') for invoice in invoices] + [dict(opening, kind='opening', number=opening['reference'] or 'Açılış bakiyesi', issued=opening['entry_date'], total=opening['amount'], has_photo=False) for opening in openings]
    obligations.sort(key=lambda value:(value['due'],value['id']))
    return dict(today=today.isoformat(), totals=totals, firms=firms, invoices=invoices, openings=openings, credits=credits, obligations=obligations, payments=payments, items=items, alerts=alerts[::-1],price_histories=price_histories,product_suggestions=suggestions,unmatched_products=unmatched,product_match_history=match_history,backup=backup_status())


def audit(db, entity, record_id, before):
    db.execute("INSERT INTO edits(entity,record_id,changed_on,before_json) VALUES(?,?,?,?)", (entity,record_id,datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(),json.dumps(before,ensure_ascii=False)))


def delete_record(entity, record_id, expected=None):
    tables = {'firm': 'firms', 'invoice': 'invoices', 'payment': 'payments', 'entry': 'account_entries'}
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
        entry_query = 'firm_id=?' if entity == 'firm' else 'invoice_id=?'
        entries = [dict(r) for r in db.execute('SELECT * FROM account_entries WHERE '+entry_query,(record_id,))] if entity in ('firm','invoice') else []
        opening_ids = [entry['id'] for entry in entries if entry['kind']=='opening']
        opening_payments = [dict(r) for opening_id in opening_ids for r in db.execute('SELECT * FROM opening_payments WHERE opening_id=?',(opening_id,))]
        counts = {'invoices':len(invoices),'payments':len(payments),'items':len(items),'entries':len(entries),'opening_payments':len(opening_payments)}
        if entity not in ('payment','entry') and any(int((expected or {}).get(key,0)) != value for key,value in counts.items()):
            raise ValueError('Bağlı kayıtlar değişmiş veya silme onayı eksik. Listeyi yenileyip silme işlemini tekrar onaylayın.')
        if entity == 'entry':
            opening_payments = [dict(r) for r in db.execute('SELECT * FROM opening_payments WHERE opening_id=?',(record_id,))]
        audit(db,entity+'_deleted',record_id,dict(record=dict(old),invoices=invoices,payments=payments,items=items,entries=entries,opening_payments=opening_payments))
        for payment in opening_payments:
            db.execute('DELETE FROM opening_payments WHERE id=?',(payment['id'],))
        for entry in entries:
            db.execute('DELETE FROM account_entries WHERE id=?',(entry['id'],))
        for invoice in invoices:
            db.execute('DELETE FROM payments WHERE invoice_id=?',(invoice['id'],))
            db.execute('DELETE FROM items WHERE invoice_id=?',(invoice['id'],))
            db.execute('DELETE FROM invoices WHERE id=?',(invoice['id'],))
        if entity=='firm':
            for review in db.execute('SELECT fingerprint,left_key FROM product_matches').fetchall():
                if json.loads(review['left_key'])[0]==record_id:
                    db.execute('DELETE FROM product_matches WHERE fingerprint=?',(review['fingerprint'],))
            db.execute('DELETE FROM firm_ocr_profiles WHERE firm_id=?',(record_id,))
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
    source_hash = photos_hash(photos)
    firm_id = int(data['firm_id'])
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
        rows.append((clean(t['product'], 'Ürün', 500), clean(t['unit'], 'Ambalaj / fiyat birimi'), cents, str(value), quantity, code, *source,row_number,line_total))
    if len({(p.casefold(), u.casefold()) for p,u,*_ in rows}) != len(rows):
        raise ValueError('Aynı ürün ve birimi faturada bir kez girin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        same_number = db.execute('SELECT id FROM invoices WHERE firm_id=? AND number=? AND id!=?',(firm_id,number,record_id or -1)).fetchone()
        if same_number:
            raise ValueError('Bu firmada aynı fatura numarası zaten kayıtlı.')
        if source_hash and db.execute('SELECT id FROM invoices WHERE source_hash=? AND id!=?',(source_hash,record_id or -1)).fetchone():
            raise ValueError('Aynı fatura dosyası daha önce kaydedilmiş.')
        similar = db.execute('SELECT number FROM invoices WHERE firm_id=? AND issued=? AND total=? AND id!=?',(firm_id,issued.isoformat(),total,record_id or -1)).fetchone()
        if similar and data.get('duplicate_confirmed') is not True:
            raise ValueError('MÜKERRER_ONAYI:'+similar['number']+' numaralı faturayla firma, tarih ve toplam aynı. Yine de kaydetmek için onaylayın.')
        values = (firm_id, number, issued.isoformat(), (issued + timedelta(days=days)).isoformat(), total, pic)
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
            credited = db.execute("SELECT COALESCE(SUM(amount),0) FROM account_entries WHERE kind='credit' AND invoice_id=?",(record_id,)).fetchone()[0]
            if total < paid + credited:
                raise ValueError('Fatura toplamı yapılan ödeme ve iadelerin altına indirilemez. Önce yanlış hareketi düzeltin.')
            before = dict(old)
            before['items'] = [dict(r) for r in db.execute('SELECT * FROM items WHERE invoice_id=?',(record_id,))]
            audit(db,'invoice',record_id,before)
            db.execute('UPDATE invoices SET firm_id=?,number=?,issued=?,due=?,total=?,photo=? WHERE id=?', (*values,record_id))
            db.execute("UPDATE account_entries SET firm_id=? WHERE kind='credit' AND invoice_id=?",(firm_id,record_id))
            db.execute('DELETE FROM items WHERE invoice_id=?',(record_id,))
        db.execute('UPDATE invoices SET extra_photos=?,import_meta=?,source_hash=? WHERE id=?',(json.dumps(photos[1:]),json.dumps(import_metadata or {}),source_hash,record_id))
        db.executemany('INSERT INTO items(invoice_id,product,unit,price,unit_price,quantity,code,source_net,source_vat,row_number,line_total) VALUES(?,?,?,?,?,?,?,?,?,?,?)', [(record_id,*r) for r in rows])
        supplier_alias = str(data.get('ocr_supplier') or '').strip()[:200]
        if supplier_alias:
            db.execute('''INSERT INTO firm_ocr_profiles(firm_id,supplier_alias,sample_count,last_seen) VALUES(?,?,1,?)
              ON CONFLICT(firm_id,supplier_alias) DO UPDATE SET sample_count=sample_count+1,last_seen=excluded.last_seen''',
              (firm_id,supplier_alias,datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()))


def create_payment(data, record_id=None):
    if data.get('target_type') == 'opening':
        return create_opening_payment(data, record_id)
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
        row = db.execute('''SELECT total-COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id AND id!=?),0)
          -COALESCE((SELECT SUM(amount) FROM account_entries WHERE kind='credit' AND invoice_id=i.id),0) AS remaining
          FROM invoices i WHERE id=?''', (record_id or -1,int(data['invoice_id']))).fetchone()
        if not row or amount > row['remaining']:
            raise ValueError('Ödeme kalan borcu aşamaz.')
        values = (int(data['invoice_id']), amount, paid_on.isoformat())
        if record_id is None:
            db.execute('INSERT INTO payments(invoice_id,amount,paid_on) VALUES(?,?,?)', values)
        else:
            audit(db,'payment',record_id,dict(old))
            db.execute('UPDATE payments SET invoice_id=?,amount=?,paid_on=? WHERE id=?',(*values,record_id))


def create_account_entry(data, record_id=None):
    kind = data.get('kind')
    if kind not in ('opening','credit'):
        raise ValueError('Geçerli bir cari hareket seçin.')
    entry_date = date.fromisoformat(data['entry_date'])
    amount = money(data['amount'])
    reference = str(data.get('reference') or '').strip()
    if len(reference) > 200:
        raise ValueError('Belge/açıklama en fazla 200 karakter olabilir.')
    firm_id = int(data['firm_id'])
    invoice_id = int(data['invoice_id']) if kind == 'credit' else None
    due = date.fromisoformat(data['due']) if kind == 'opening' else entry_date
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM account_entries WHERE id=?',(record_id,)).fetchone() if record_id else None
        if record_id and not old:
            raise ValueError('Cari hareket bulunamadı.')
        if old and old['kind'] != kind:
            raise ValueError('Açılış bakiyesi ile iade kaydı birbirine dönüştürülemez.')
        if reference and db.execute('''SELECT id FROM account_entries
          WHERE firm_id=? AND kind=? AND entry_date=? AND amount=? AND reference=? AND id!=?''',
          (firm_id,kind,entry_date.isoformat(),amount,reference,record_id or -1)).fetchone():
            raise ValueError('Aynı firma, tarih, tutar ve belge numarasıyla bu cari hareket zaten kayıtlı.')
        if kind == 'credit':
            invoice = db.execute('''SELECT i.firm_id,i.total-COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0)
              -COALESCE((SELECT SUM(amount) FROM account_entries WHERE kind='credit' AND invoice_id=i.id AND id!=?),0) AS remaining
              FROM invoices i WHERE i.id=?''',(record_id or -1,invoice_id)).fetchone()
            if not invoice or invoice['firm_id'] != firm_id:
                raise ValueError('İade/alacak kaydı için seçilen fatura firmayla uyuşmuyor.')
            if amount > invoice['remaining']:
                raise ValueError('İade/alacak tutarı faturanın kalan borcunu aşamaz.')
        if kind == 'opening' and record_id:
            paid = db.execute('SELECT COALESCE(SUM(amount),0) FROM opening_payments WHERE opening_id=?',(record_id,)).fetchone()[0]
            if amount < paid:
                raise ValueError('Açılış bakiyesi, yapılan ödemelerin altına indirilemez.')
        values=(firm_id,invoice_id,kind,entry_date.isoformat(),due.isoformat(),amount,reference)
        if record_id:
            audit(db,'account_entry',record_id,dict(old))
            db.execute('UPDATE account_entries SET firm_id=?,invoice_id=?,kind=?,entry_date=?,due=?,amount=?,reference=? WHERE id=?',(*values,record_id))
        else:
            db.execute('INSERT INTO account_entries(firm_id,invoice_id,kind,entry_date,due,amount,reference) VALUES(?,?,?,?,?,?,?)',values)


def create_opening_payment(data, record_id=None):
    amount = money(data['amount'])
    paid_on = date.fromisoformat(data['paid_on'])
    if paid_on > datetime.now(ZoneInfo('Europe/Istanbul')).date():
        raise ValueError('Ödeme tarihi gelecekte olamaz.')
    opening_id = int(data.get('target_id') or data.get('opening_id'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM opening_payments WHERE id=?',(record_id,)).fetchone() if record_id else None
        if record_id and not old:
            raise ValueError('Ödeme bulunamadı.')
        row = db.execute('''SELECT a.amount-COALESCE((SELECT SUM(amount) FROM opening_payments WHERE opening_id=a.id AND id!=?),0) AS remaining
          FROM account_entries a WHERE a.id=? AND a.kind='opening' ''',(record_id or -1,opening_id)).fetchone()
        if not row or amount > row['remaining']:
            raise ValueError('Ödeme açılış bakiyesinin kalan borcunu aşamaz.')
        values=(opening_id,amount,paid_on.isoformat())
        if record_id:
            audit(db,'opening_payment',record_id,dict(old))
            db.execute('UPDATE opening_payments SET opening_id=?,amount=?,paid_on=? WHERE id=?',(*values,record_id))
        else:
            db.execute('INSERT INTO opening_payments(opening_id,amount,paid_on) VALUES(?,?,?)',values)


def delete_payment(data):
    if data.get('target_type') != 'opening':
        return delete_record('payment',int(data['id']))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM opening_payments WHERE id=?',(int(data['id']),)).fetchone()
        if not old:
            raise ValueError('Ödeme bulunamadı.')
        audit(db,'opening_payment_deleted',int(data['id']),dict(old))
        db.execute('DELETE FROM opening_payments WHERE id=?',(int(data['id']),))


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
        if self.path.startswith('/api/draft/'):
            try:
                return self.respond({'draft': load_draft(unquote(self.path[len('/api/draft/'):]))})
            except ValueError as error:
                return self.respond({'error': str(error)}, 400)
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
            elif self.path == '/api/account-entries':
                create_account_entry(data)
            elif self.path == '/api/account-entries/edit':
                create_account_entry(data, int(data['id']))
            elif self.path == '/api/drafts':
                if data.get('action') == 'clear':
                    clear_draft(data.get('key'))
                else:
                    save_draft(data.get('key'), data.get('payload'))
            elif self.path == '/api/account-entries/delete':
                delete_record('entry',int(data['id']))
            elif self.path == '/api/firms/delete':
                delete_record('firm',int(data['id']),data.get('expected'))
            elif self.path == '/api/invoices/delete':
                delete_record('invoice',int(data['id']),data.get('expected'))
            elif self.path == '/api/payments/delete':
                delete_payment(data)
            elif self.path == '/api/backups/restore':
                restore_backup(data.get('name'))
            elif self.path == '/api/backups/directory':
                if data.get('action') == 'reset':
                    set_backup_directory()
                elif data.get('action') == 'choose':
                    if choose_backup_directory() is None:
                        raise ValueError('Klasör seçimi iptal edildi; mevcut yedek konumu değişmedi.')
                else:
                    raise ValueError('Geçerli bir yedek klasörü işlemi seçin.')
                automatic_backup()
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
                executable, lang = ocr_engine()
                with connect() as db:
                    firms = [dict(r) for r in db.execute('''SELECT id,name FROM firms
                      UNION ALL SELECT firm_id AS id,supplier_alias AS name FROM firm_ocr_profiles''')]
                readings, visible_text = [], []
                pdf_layers = data.get('pdf_text_layers') or []
                pdf_readings, pdf_text = pdf_text_readings(pdf_layers, firms)
                if pdf_readings:
                    readings.extend(pdf_readings)
                    visible_text.append('--- PDF içindeki orijinal metin ---\n'+pdf_text)

                def add_layout(mode, with_tsv=True, supplemental=False):
                    existing = {
                        item['row_number']: item
                        for reading in readings for item in reading.get('items', [])
                    }
                    def core(item):
                        return tuple(str(item.get(key,'')) for key in ('code','quantity','line_total','net','vat'))
                    for index, pic in enumerate(photos, 1):
                        page_text, page_tsv = run_tesseract(pic, lang, mode, with_tsv, executable)
                        visible_text.append(f'--- Görsel {index} · OCR düzeni {mode} ---\n{page_text}')
                        plain = parse_invoice(page_text, firms)
                        if page_tsv:
                            positioned = tsv_reading(page_tsv)
                            if supplemental:
                                conflicting = []
                                kept = []
                                for item in positioned['items']:
                                    previous = existing.get(item['row_number'])
                                    if previous is None:
                                        kept.append(item)
                                        existing.setdefault(item['row_number'],item)
                                    elif core(previous) != core(item):
                                        conflicting.append(item['row_number'])
                                positioned['items'] = kept
                                if conflicting:
                                    positioned['table_warnings'].append(
                                        'Ek OCR düzeninde farklı okunan satırlar ilk güvenilir okumayı değiştirmedi: '
                                        + ', '.join(map(str,sorted(set(conflicting)))) + '.'
                                    )
                            positioned_rows = {item['row_number'] for item in positioned['items']}
                            # Keep plain-text rows only when the coordinate reading
                            # did not already reconstruct that invoice row.
                            plain['items'] = [
                                item for item in plain['items']
                                if item['row_number'] not in positioned_rows
                            ]
                            readings.append(positioned)
                        if supplemental:
                            plain['items'] = [
                                item for item in plain['items']
                                if item['row_number'] not in existing
                            ]
                            for item in plain['items']:
                                existing.setdefault(item['row_number'],item)
                        readings.append(plain)

                # PSM 4 keeps ruled invoice tables as columns; TSV coordinates put
                # the cells back into rows. Sparse text is a conservative fallback.
                add_layout(4, supplemental=bool(pdf_readings))
                draft = merge_readings(readings)
                def table_errors(value):
                    return [
                        error for error in value['coverage']['errors']
                        if error != 'Fatura toplamı okunamadı veya eksik.'
                    ]
                if not draft['items'] or table_errors(draft):
                    add_layout(11, supplemental=True)
                    draft = merge_readings(readings)
                if not draft['items'] or table_errors(draft):
                    add_layout(6, supplemental=True)
                    draft = merge_readings(readings)
                if not all(draft[key] for key in ('number', 'issued', 'total')):
                    for index, pic in enumerate(photos, 1):
                        page_text, _ = run_tesseract(pic, lang, 3, False, executable)
                        visible_text.append(f'--- Görsel {index} · üst bilgi okuması ---\n{page_text}')
                        readings.append(parse_invoice(page_text, firms))
                        draft = merge_readings(readings)
                        if all(draft[key] for key in ('number', 'issued', 'total')):
                            break
                if not any(value.partition('\n')[2].strip() for value in visible_text):
                    raise ValueError(
                        'Fotoğrafta okunabilir yazı bulunamadı. Faturayı dik tutup yansıma olmadan, '
                        'yazılar net görünecek yakınlıkta yeniden çekin.'
                    )
                return self.respond({'text':'\n'.join(visible_text),'language':lang,'draft':draft})
            else:
                return self.respond({'error':'Bulunamadı.'},404)
            self.respond({'ok':True},201)
        except sqlite3.IntegrityError:
            self.respond({'error':'Firma veya fatura zaten kayıtlı ya da firma seçimi geçersiz.'},400)
        except sqlite3.DatabaseError:
            self.respond({'error':'Veritabanı veya yedek dosyası okunamadı; mevcut kayıtlar değiştirilmedi.'},400)
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
    port = int(os.environ.get('PORT','8000'))
    print(f'Market Cari geliştirme sunucusu: 127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
