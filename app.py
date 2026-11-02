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
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('MARKET_DB', str(ROOT / 'data/market.sqlite3')))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init():
    with connect() as db:
        db.executescript('''
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


def state(today=None):
    today = today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    with connect() as db:
        firms = [dict(r) for r in db.execute('SELECT * FROM firms ORDER BY name')]
        invoices = [dict(r) for r in db.execute('''SELECT i.id,i.firm_id,f.name AS firm,i.number,i.issued,i.due,i.total,
          COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) AS paid,
          photo IS NOT NULL AS has_photo FROM invoices i JOIN firms f ON f.id=i.firm_id ORDER BY due,i.id''')]
        payments = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY paid_on DESC,id DESC')]
        reviews = {r['fingerprint'] for r in db.execute('SELECT fingerprint FROM price_reviews')}
        items = [dict(r) for r in db.execute('''SELECT t.*,i.firm_id,i.issued,i.number,f.name AS firm FROM items t
          JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id ORDER BY i.issued,i.id,t.id''')]
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
    previous, alerts = {}, []
    for item in items:
        key = (item['firm_id'], item['product'].casefold(), item['unit'].casefold())
        old = previous.get(key)
        if old and old['invoice_id'] != item['invoice_id'] and old['price'] != item['price']:
            fingerprint = hashlib.sha256(json.dumps([key,old['invoice_id'],old['issued'],old['price'],item['invoice_id'],item['issued'],item['price']],ensure_ascii=False).encode()).hexdigest()
            alerts.append(dict(item, fingerprint=fingerprint, approved=fingerprint in reviews, old_price=old['price'], change=round((item['price'] / old['price'] - 1) * 100, 2)))
        previous[key] = item
    for f in firms:
        f['balance'] = sum(i['remaining'] for i in invoices if i['firm_id'] == f['id'])
    return dict(today=today.isoformat(), totals=totals, firms=firms, invoices=invoices, payments=payments, items=items, alerts=alerts[::-1])


def audit(db, entity, record_id, before):
    db.execute("INSERT INTO edits(entity,record_id,changed_on,before_json) VALUES(?,?,?,?)", (entity,record_id,datetime.now(ZoneInfo("Europe/Istanbul")).isoformat(),json.dumps(before,ensure_ascii=False)))


def delete_firm(record_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM firms WHERE id=?', (record_id,)).fetchone()
        if not old:
            raise ValueError('Firma bulunamadı.')
        if db.execute('SELECT 1 FROM invoices WHERE firm_id=? LIMIT 1', (record_id,)).fetchone():
            raise ValueError('Bu firmaya ait fatura kayıtları var. Ödenmiş faturalar dahil kayıtları korumak için firma silinemez. Firma adını Düzelt ile değiştirebilirsiniz.')
        audit(db, 'firm_deleted', record_id, dict(old))
        db.execute('DELETE FROM firms WHERE id=?', (record_id,))


def create_invoice(data, record_id=None):
    issued = date.fromisoformat(data['issued'])
    days = int(data['days'])
    if days < 0 or days > 3650:
        raise ValueError('Vade 0 ile 3650 gün arasında olmalı.')
    total = money(data['total'])
    number = clean(data['number'], 'Fatura numarası')
    pic = photo(data.get('photo'))
    items = data.get('items', [])
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError('Ürün listesi geçersiz.')
    rows = [(clean(t['product'], 'Ürün'), clean(t['unit'], 'Ambalaj / fiyat birimi'), money(t['price'])) for t in items]
    if len({(p.casefold(), u.casefold()) for p,u,_ in rows}) != len(rows):
        raise ValueError('Aynı ürün ve birimi faturada bir kez girin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        values = (int(data['firm_id']), number, issued.isoformat(), (issued + timedelta(days=days)).isoformat(), total, pic)
        if record_id is None:
            cur = db.execute('INSERT INTO invoices(firm_id,number,issued,due,total,photo) VALUES(?,?,?,?,?,?)', values)
            record_id = cur.lastrowid
        else:
            old = db.execute('SELECT * FROM invoices WHERE id=?',(record_id,)).fetchone()
            if not old:
                raise ValueError('Fatura bulunamadı.')
            paid = db.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?',(record_id,)).fetchone()[0]
            if total < paid:
                raise ValueError('Fatura toplamı yapılan ödemelerden az olamaz. Önce yanlış ödemeyi düzeltin.')
            before = dict(old)
            before['items'] = [dict(r) for r in db.execute('SELECT * FROM items WHERE invoice_id=?',(record_id,))]
            audit(db,'invoice',record_id,before)
            db.execute('UPDATE invoices SET firm_id=?,number=?,issued=?,due=?,total=?,photo=? WHERE id=?', (*values,record_id))
            db.execute('DELETE FROM items WHERE invoice_id=?',(record_id,))
        db.executemany('INSERT INTO items(invoice_id,product,unit,price) VALUES(?,?,?,?)', [(record_id,*r) for r in rows])


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
                    row = db.execute('SELECT photo FROM invoices WHERE id=?', (int(self.path.rsplit('/',1)[1]),)).fetchone()
                return self.respond({'photo': row['photo'] if row else None})
            except ValueError:
                return self.respond({'error': 'Geçersiz kayıt.'}, 400)
        files = {'/': ('index.html','text/html'), '/app.js': ('app.js','text/javascript'), '/style.css': ('style.css','text/css'), '/logo.png': ('logo.png','image/png')}
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
                delete_firm(int(data['id']))
            elif self.path == '/api/firms/edit':
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    old = db.execute('SELECT * FROM firms WHERE id=?',(int(data['id']),)).fetchone()
                    if not old:
                        raise ValueError('Firma bulunamadı.')
                    audit(db,'firm',int(data['id']),dict(old))
                    db.execute('UPDATE firms SET name=? WHERE id=?',(clean(data['name'],'Firma adı'),int(data['id'])))
            elif self.path == '/api/prices/approve':
                fingerprint = data['fingerprint']
                if fingerprint not in {a['fingerprint'] for a in state()['alerts']}:
                    raise ValueError('Fiyat değişimi güncellenmiş. Listeyi yenileyip tekrar kontrol edin.')
                with connect() as db:
                    db.execute('INSERT OR IGNORE INTO price_reviews VALUES(?,?)',(fingerprint,datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()))
            elif self.path == '/api/ocr':
                pic = photo(data.get('photo'))
                if not pic:
                    raise ValueError('Önce bir fotoğraf seçin.')
                with tempfile.NamedTemporaryFile(suffix='.img') as f:
                    f.write(base64.b64decode(pic.split(',',1)[1])); f.flush()
                    languages = subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,check=True).stdout
                    lang = 'tur+eng' if 'tur' in languages.split() else 'eng'
                    result = subprocess.run(['tesseract',f.name,'stdout','-l',lang],capture_output=True,text=True,timeout=45)
                    if result.returncode:
                        raise ValueError('Fotoğraf okunamadı. Daha net bir JPEG veya PNG deneyin.')
                return self.respond({'text':result.stdout,'language':lang})
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
    init()
    print('Market Cari geliştirme sunucusu: 127.0.0.1:8000',flush=True)
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','8000'))),Handler).serve_forever()
