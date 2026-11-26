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
from calendar import monthrange
from threading import Event, Lock, Thread
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from invoice_parser import parse_invoice, check_items, merge_readings, normalize_ettn
from invoice_ocr import parse_tsv_table
from product_matching import compare_items, compare_suppliers, compare_package_prices, identity as product_identity, pair_key
from reports import range_report, remaining_after_firm_payment

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
DB = Path(os.environ.get('MARKET_DB', str(ROOT / 'data/market.sqlite3')))
try:
    ISTANBUL = ZoneInfo('Europe/Istanbul')
except ZoneInfoNotFoundError:
    # Turkey has used permanent UTC+3 since 2016. This fallback keeps the
    # packaged Windows application usable even if OS time-zone data is absent.
    ISTANBUL = timezone(timedelta(hours=3), name='Europe/Istanbul')


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


backup_lock = Lock()
backup_error = ''


def automatic_backup(now=None, keep=30):
    global backup_error
    with backup_lock:
        try:
            result = write_backup(now, keep)
        except (OSError, sqlite3.Error) as error:
            backup_error = str(error)
            raise
        backup_error = ''
        return result


def start_periodic_backup(interval=3600):
    stop = Event()

    def run():
        while not stop.wait(interval):
            try:
                automatic_backup()
            except (OSError, sqlite3.Error):
                continue

    Thread(target=run, name='market-backup', daemon=True).start()
    return stop


def write_backup(now, keep):
    """Create a consistent startup snapshot and retain the newest snapshots."""
    if not DB.exists() or not DB.stat().st_size:
        return None
    now = now or datetime.now(ISTANBUL)
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('market-' + now.strftime('%Y-%m-%d_%H-%M-%S-%f') + '.sqlite3')
    if target.exists():
        return target
    temporary = target.with_suffix('.tmp')
    try:
        # sqlite3's context manager commits but does not close the connection.
        # Explicit closing is required before Windows can rename the backup.
        with closing(sqlite3.connect(DB)) as source, closing(sqlite3.connect(temporary)) as destination:
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
    with backup_lock:
        backups = sorted(directory.glob('market-*.sqlite3'), reverse=True)
        files = []
        latest_at = ''
        for path in backups:
            try:
                details = path.stat()
            except OSError:
                continue
            if not files:
                latest_at = datetime.fromtimestamp(details.st_mtime, ISTANBUL).isoformat()
            files.append({'name': path.name, 'size': details.st_size})
        error = backup_error
    return {
        'directory': str(directory),
        'latest': files[0]['name'] if files else '',
        'latest_at': latest_at,
        'count': len(files),
        'files': files,
        'error': error,
    }


def restore_backup(name):
    """Validate and atomically restore a selected application backup."""
    if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r'market-[0-9_-]+\.sqlite3', name):
        raise ValueError('Geçerli bir yedek seçin.')
    source_path = backup_directory() / name
    if not source_path.is_file():
        raise ValueError('Yedek dosyası bulunamadı.')
    with tempfile.TemporaryDirectory(prefix='.market-restore-', dir=DB.parent) as folder:
        temporary = Path(folder) / DB.name
        try:
            with closing(sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro', uri=True)) as source:
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'firms','invoices','items','payments'} <= tables:
                    raise ValueError('Seçilen dosya Sancaklı Market yedeği değil.')
                with closing(connect(temporary)) as destination:
                    source.backup(destination)
            with closing(connect(temporary)) as restored, restored:
                if restored.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Seçilen yedek bütünlük kontrolünden geçemedi.')
                initialize_schema(restored)
                with closing(sqlite3.connect(':memory:')) as reference:
                    initialize_schema(reference)
                    expected = reference.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    restored_tables = {row[0] for row in restored.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    for (table,) in expected:
                        columns = {row[1] for row in reference.execute(f'PRAGMA table_info({table})')}
                        actual = {row[1] for row in restored.execute(f'PRAGMA table_info({table})')}
                        if table not in restored_tables or not columns <= actual:
                            raise ValueError('Seçilen yedekte gerekli kayıt alanları eksik.')
                if restored.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Seçilen yedekte bağlı kayıtlar tutarsız.')
            state(include_analysis=False, db_path=temporary)
        except (sqlite3.DatabaseError, InvalidOperation) as error:
            raise ValueError('Seçilen yedek açılamadı. Mevcut kayıtlar değiştirilmedi.') from error
        automatic_backup()
        os.replace(temporary, DB)


def connect(path=None):
    path = DB if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init():
    with closing(connect()) as db, db:
        initialize_schema(db)


def initialize_schema(db):
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
    CREATE TABLE IF NOT EXISTS firm_payments(id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
      amount INTEGER NOT NULL CHECK(amount>0), paid_on TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS firm_payments_firm ON firm_payments(firm_id);
    CREATE TABLE IF NOT EXISTS account_entries(id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL REFERENCES firms(id),
      invoice_id INTEGER REFERENCES invoices(id), kind TEXT NOT NULL CHECK(kind IN ('opening','credit')),
      entry_date TEXT NOT NULL, due TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), reference TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS opening_payments(id INTEGER PRIMARY KEY, opening_id INTEGER NOT NULL REFERENCES account_entries(id),
      amount INTEGER NOT NULL CHECK(amount>0), paid_on TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS firm_ocr_profiles(firm_id INTEGER NOT NULL REFERENCES firms(id),supplier_alias TEXT NOT NULL,
      sample_count INTEGER NOT NULL DEFAULT 1,last_seen TEXT NOT NULL,PRIMARY KEY(firm_id,supplier_alias));
    CREATE TABLE IF NOT EXISTS form_drafts(draft_key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_on TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS restored_deletions(edit_id INTEGER PRIMARY KEY REFERENCES edits(id));
    CREATE INDEX IF NOT EXISTS payments_invoice ON payments(invoice_id);
    CREATE INDEX IF NOT EXISTS items_invoice ON items(invoice_id);
    CREATE INDEX IF NOT EXISTS entries_invoice ON account_entries(invoice_id);
    CREATE INDEX IF NOT EXISTS opening_payments_entry ON opening_payments(opening_id);
    ''')
    if 'archived' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute('ALTER TABLE firms ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
    if 'default_due_days' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute('ALTER TABLE firms ADD COLUMN default_due_days INTEGER CHECK(default_due_days BETWEEN 0 AND 3650)')
    if 'phone' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute("ALTER TABLE firms ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    if 'merged_into' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute('ALTER TABLE firms ADD COLUMN merged_into INTEGER REFERENCES firms(id)')
    if 'tax_number' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute("ALTER TABLE firms ADD COLUMN tax_number TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS firms_tax_number
                ON firms(tax_number) WHERE tax_number<>'' AND merged_into IS NULL""")
    if 'ocr_amount_order' not in {r[1] for r in db.execute('PRAGMA table_info(firms)')}:
        db.execute("ALTER TABLE firms ADD COLUMN ocr_amount_order TEXT NOT NULL DEFAULT ''")
    if 'identity' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
        db.execute("ALTER TABLE invoices ADD COLUMN identity TEXT NOT NULL DEFAULT ''")
    db.execute("UPDATE invoices SET identity=lower(hex(randomblob(16))) WHERE identity=''")
    if 'import_meta' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
        db.execute("ALTER TABLE invoices ADD COLUMN import_meta TEXT NOT NULL DEFAULT '{}'")
    if 'extra_photos' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
        db.execute("ALTER TABLE invoices ADD COLUMN extra_photos TEXT NOT NULL DEFAULT '[]'")
    if 'source_hash' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
        db.execute("ALTER TABLE invoices ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''")
    if 'ettn' not in {r[1] for r in db.execute('PRAGMA table_info(invoices)')}:
        db.execute("ALTER TABLE invoices ADD COLUMN ettn TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS invoices_ettn ON invoices(ettn) WHERE ettn<>''")
    if 'unit_price' not in {r[1] for r in db.execute('PRAGMA table_info(items)')}:
        db.execute("ALTER TABLE items ADD COLUMN unit_price TEXT NOT NULL DEFAULT ''")
    db.execute("UPDATE items SET unit_price=printf('%.4f',price/100.0) WHERE unit_price=''")
    item_columns = {r[1] for r in db.execute('PRAGMA table_info(items)')}
    if 'gift_quantity' not in item_columns:
        db.execute("ALTER TABLE items ADD COLUMN gift_quantity TEXT NOT NULL DEFAULT '0'")
    if 'gift_only' not in item_columns:
        db.execute("ALTER TABLE items ADD COLUMN gift_only INTEGER NOT NULL DEFAULT 0 CHECK(gift_only IN (0,1))")
    for column in ('quantity','code','source_net','source_vat','row_number','line_total'):
        if column not in item_columns:
            db.execute(f"ALTER TABLE items ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    migrate_invoice_drafts(db)


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


def firm_tax_number(db, value, firm_id=None):
    if not isinstance(value, str):
        raise ValueError('Vergi numarası 10 rakam olmalı.')
    value = value.strip()
    if value and not re.fullmatch(r'[0-9]{10}', value):
        raise ValueError('Vergi numarası 10 rakam olmalı.')
    if value and db.execute('SELECT 1 FROM firms WHERE tax_number=? AND merged_into IS NULL AND id!=?',
                            (value, firm_id or -1)).fetchone():
        raise ValueError('Bu vergi numarası başka bir firmada kayıtlı.')
    return value


def firm_phone(value):
    if not isinstance(value, str):
        raise ValueError('Geçerli bir telefon numarası girin.')
    value = value.strip()
    if value and (len(value) > 40 or not re.fullmatch(r'\+?[0-9 ().-]+', value)
                  or not 3 <= len(re.sub(r'[^0-9]', '', value)) <= 15):
        raise ValueError('Telefon numarası 3 ile 15 rakam içermeli. Boş bırakabilirsiniz.')
    return value


def default_due_days(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,4}', str(value)):
        raise ValueError('Varsayılan vade 0 ile 3650 arasında tam gün olmalı.')
    days = int(value)
    if days > 3650:
        raise ValueError('Varsayılan vade 0 ile 3650 arasında tam gün olmalı.')
    return days


def clean(value, label, max_length=200):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f'{label} alanını doldurun (en fazla {max_length} karakter).')
    return value.strip()


def draft_key(value):
    value = str(value or '')
    if not re.fullmatch(r'(?:(?:firm|invoice|payment|entry):(?:new|[1-9][0-9]*)|payment:(?:firm|invoice|opening):[1-9][0-9]*|invoice:uid:[0-9a-f]{32})', value):
        raise ValueError('Taslak türü geçersiz.')
    return value


def deleted_invoice_ids(db):
    deleted_ids = set()
    for entity, record_id, snapshot in db.execute(
        "SELECT entity,record_id,before_json FROM edits WHERE entity IN ('invoice_deleted','firm_deleted')"
    ):
        if entity == 'invoice_deleted':
            deleted_ids.add(record_id)
        else:
            deleted_ids.update(invoice['id'] for invoice in json.loads(snapshot).get('invoices', []))
    return deleted_ids


def migrate_invoice_drafts(db):
    legacy = db.execute("SELECT draft_key FROM form_drafts WHERE draft_key GLOB 'invoice:[0-9]*'").fetchall()
    if not legacy:
        return
    deleted_ids = deleted_invoice_ids(db)
    for (key,) in legacy:
        invoice_id = key.split(':', 1)[1]
        if not invoice_id.isdecimal():
            continue
        invoice_id = int(invoice_id)
        invoice = db.execute('SELECT identity FROM invoices WHERE id=?', (invoice_id,)).fetchone()
        if invoice and invoice_id not in deleted_ids:
            db.execute("UPDATE OR IGNORE form_drafts SET draft_key=? WHERE draft_key=?",
                       ('invoice:uid:'+invoice[0], key))
        db.execute('DELETE FROM form_drafts WHERE draft_key=?', (key,))


def resolve_draft_key(db, key):
    legacy = re.fullmatch(r'invoice:([1-9][0-9]*)', key)
    if legacy:
        invoice_id = int(legacy[1])
        if invoice_id > 2**63-1 or invoice_id in deleted_invoice_ids(db):
            return None
        invoice = db.execute('SELECT identity FROM invoices WHERE id=?', (invoice_id,)).fetchone()
        return 'invoice:uid:'+invoice['identity'] if invoice else None
    if key.startswith('invoice:uid:'):
        identity = key.removeprefix('invoice:uid:')
        if not db.execute('SELECT 1 FROM invoices WHERE identity=?', (identity,)).fetchone():
            return None
    return key


def save_draft(key, payload):
    key = draft_key(key)
    if not isinstance(payload, dict):
        raise ValueError('Taslak içeriği geçersiz.')
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(serialized.encode('utf-8')) > 11 * 1024 * 1024:
        raise ValueError('Taslak en fazla 11 MB olabilir.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        key = resolve_draft_key(db, key)
        if key is None:
            raise ValueError('Taslağın ait olduğu fatura artık kayıtlı değil.')
        db.execute('''INSERT INTO form_drafts(draft_key,payload,updated_on) VALUES(?,?,?)
          ON CONFLICT(draft_key) DO UPDATE SET payload=excluded.payload,updated_on=excluded.updated_on''',
          (key, serialized, datetime.now(ISTANBUL).isoformat()))


def load_draft(key):
    key = draft_key(key)
    with connect() as db:
        key = resolve_draft_key(db, key)
        row = db.execute('SELECT payload,updated_on FROM form_drafts WHERE draft_key=?', (key,)).fetchone()
    return dict(payload=json.loads(row['payload']), updated_on=row['updated_on']) if row else None


def clear_draft(key):
    key = draft_key(key)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        key = resolve_draft_key(db, key)
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


def ocr_photo_variants(photos):
    """Crop narrow paper and add rotations for sideways phone photos."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return photos
    primary=[]
    rotated=[]
    for value in photos:
        try:
            raw=base64.b64decode(value.split(',',1)[1],validate=True)
            with Image.open(io.BytesIO(raw)) as opened:
                source=ImageOps.exif_transpose(opened).convert('RGB')
            orientations=[source]
            if source.width>source.height*1.12:
                orientations.extend([source.rotate(90,expand=True,fillcolor='white'),source.rotate(270,expand=True,fillcolor='white')])
            for orientation_index,image in enumerate(orientations):
                scan_width=min(240,image.width)
                scan_height=max(1,round(image.height*scan_width/image.width))
                scan=image.resize((scan_width,scan_height),Image.Resampling.BILINEAR).convert('L')
                pixels=scan.load()
                columns=[]
                for x in range(scan_width):
                    columns.append(sum(pixels[x,y]>=180 for y in range(scan_height))/scan_height>=.22)
                runs=[];start=None;last=None;gap=max(2,round(scan_width*.015))
                for x in range(scan_width+1):
                    if x<scan_width and columns[x]:
                        if start is None:start=x
                        last=x
                    if start is not None and (x==scan_width or x-last>gap):
                        runs.append((start,last+1));start=last=None
                if runs:
                    left,right=max(runs,key=lambda pair:pair[1]-pair[0])
                    if right-left>=scan_width*.20:
                        margin=max(2,round(scan_width*.015));left=max(0,left-margin);right=min(scan_width,right+margin)
                        if left>scan_width*.03 or right<scan_width*.97:
                            scale=image.width/scan_width
                            image=image.crop((round(left*scale),0,round(right*scale),image.height))
                image.thumbnail((2400,5200),Image.Resampling.LANCZOS)
                output=io.BytesIO();image.save(output,'JPEG',quality=86,optimize=True)
                encoded='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
                target=primary if orientation_index==0 else rotated
                if encoded not in primary and encoded not in rotated:target.append(encoded)
        except (OSError,ValueError):
            if value not in primary:primary.append(value)
    # Never sacrifice a user-selected page to make room for a rotated copy.
    # Rotations are supplemental and use only the remaining OCR budget.
    return (primary+rotated[:max(0,6-len(primary))]) or photos


def tsv_reading(tsv_text, amount_order=None):
    """Adapt coordinate-based OCR rows to the normal invoice reading shape."""
    table = parse_tsv_table(tsv_text, amount_order)
    return dict(
        number='', issued='', total='', supplier='', firm_id=None,
        items=table['items'], warnings=[], table_warnings=table['warnings'],
        sources={}, import_check={'expected_rows': table['expected_rows'],'detected_rows':table.get('detected_rows',[]),'line_total_kind':'net'},
        labeled_pages=table['labeled_pages'],
        table_pages=table['table_pages'],
    )


def pdf_text_readings(layers, firms, coordinates=None):
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
    coordinate_text = '\n'.join(tsv)
    if coordinates is not None:
        coordinates.append(coordinate_text)
    positioned = tsv_reading(coordinate_text)
    plain = parse_invoice('\n'.join(visible_pages), firms)
    if positioned['labeled_pages'] == len(layers) and positioned['import_check']['detected_rows'] == positioned['import_check']['expected_rows']:
        plain['items'] = []
        plain['import_check'] = {}
    positioned_rows={item['row_number'] for item in positioned['items']}
    plain['items']=[item for item in plain['items'] if item['row_number'] not in positioned_rows]
    return [positioned,plain], '\n'.join(visible_pages)


def apply_supplier_layout(draft, coordinates, layouts, selected_firm=None, pdf_coordinates=()):
    detected = draft['firm_id']
    if selected_firm and detected and selected_firm != detected:
        return dict(draft, warnings=draft['warnings'] + [
            'Seçilen okuma firması belgeyle uyuşmadı. Genel okuma gösteriliyor.'
        ])
    firm_id = selected_firm or detected
    if firm_id not in layouts:
        return draft
    name, order = layouts[firm_id]
    header = dict(draft, firm_id=firm_id, items=[], warnings=[])
    pdf_readings = [tsv_reading(text, order) for text in pdf_coordinates]
    custom = merge_readings([header, *pdf_readings])
    if not custom['items'] or custom['coverage']['errors']:
        pdf_rows = {item['row_number'] for item in custom['items']}
        photo_readings = [tsv_reading(text, order) for text in coordinates]
        for reading in photo_readings:
            reading['items'] = [item for item in reading['items'] if item['row_number'] not in pdf_rows]
        custom = merge_readings([header, *pdf_readings, *photo_readings])
    if custom['items'] and not custom['coverage']['errors']:
        return dict(custom, warnings=[f'{name} için kayıtlı KDV/tutar sütun düzeni uygulandı.'] + custom['warnings'])
    return dict(draft, warnings=draft['warnings'] + [
        f'{name} için kayıtlı sütun düzeni fatura toplamıyla doğrulanamadı. Genel okuma gösteriliyor.'
    ])


def verified_pdf_draft(readings):
    positioned, header = readings
    check = positioned['import_check']
    if (not positioned['labeled_pages'] or positioned['labeled_pages'] != positioned['table_pages']
            or positioned['table_warnings']
            or check['expected_rows'] != check['detected_rows']
            or any(not row['code'] or row['review'] for row in positioned['items'])):
        return None
    # Use plain text for the header only. Every item must come from table cells.
    draft = merge_readings([positioned, dict(header, items=[], import_check={})])
    if draft['items'] and not draft['coverage']['errors'] and all(draft[key] for key in ('number', 'issued', 'total')):
        return draft
    return None


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


def monthly_purchases(month):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
        raise ValueError('Geçerli bir alış ayı seçin.')
    first = date.fromisoformat(month + '-01')
    prior = (first - timedelta(days=1)).replace(day=1) if first > date.min else first
    last = first.replace(day=monthrange(first.year, first.month)[1])
    with closing(connect()) as db:
        rows = db.execute('''
            WITH movements AS (
                SELECT firm_id, issued AS day, total AS purchases, 0 AS returns
                FROM invoices WHERE issued BETWEEN :start AND :end
                UNION ALL
                SELECT firm_id, entry_date, 0, amount
                FROM account_entries WHERE kind='credit' AND entry_date BETWEEN :start AND :end
            )
            SELECT f.id, f.name, f.archived,
                SUM(CASE WHEN day >= :first THEN purchases ELSE 0 END) AS purchases,
                SUM(CASE WHEN day >= :first THEN returns ELSE 0 END) AS returns,
                SUM(CASE WHEN day < :first THEN purchases ELSE 0 END) AS previous_purchases,
                SUM(CASE WHEN day < :first THEN returns ELSE 0 END) AS previous_returns
            FROM movements JOIN firms f ON f.id=movements.firm_id
            GROUP BY f.id
            ORDER BY purchases DESC, previous_purchases DESC, f.name
        ''', {'start': prior.isoformat(), 'end': last.isoformat(), 'first': first.isoformat()})
        suppliers = [dict(row) for row in rows]
    fields = ('purchases', 'returns', 'previous_purchases', 'previous_returns')
    totals = {key: sum(row[key] for row in suppliers) for key in fields}
    for row in [totals, *suppliers]:
        row['net'] = row['purchases'] - row['returns']
        row['previous_net'] = row['previous_purchases'] - row['previous_returns']
        row['change'] = row['net'] - row['previous_net']
        row['change_percent'] = round(row['change'] * 100 / row['previous_net'], 2) if row['previous_net'] > 0 else None
    return dict(month=month, previous_month=prior.isoformat()[:7] if prior != first else None,
                totals=totals, suppliers=suppliers)


def whole_lira_price_changed(item, previous):
    return int(Decimal(item['unit_price'])) != int(Decimal(previous['unit_price']))


def invoice_price_change_count(invoice_id):
    with closing(connect()) as db, db:
        db.execute('BEGIN')
        invoice = db.execute('SELECT firm_id,issued FROM invoices WHERE id=?', (invoice_id,)).fetchone()
        if not invoice:
            raise ValueError('Fatura bulunamadı.')
        items = [dict(row) for row in db.execute('''SELECT t.*,i.firm_id,i.issued
            FROM items t JOIN invoices i ON i.id=t.invoice_id
            WHERE i.firm_id=? AND (i.issued,i.id)<=(?,?) AND t.gift_only=0
            ORDER BY i.issued,i.id,t.id''', (invoice['firm_id'], invoice['issued'], invoice_id))]
        decisions = [dict(row) for row in db.execute('SELECT * FROM product_matches')]
    comparisons, _, _ = compare_items(items, decisions, include_suggestions=False)
    return sum(row['current']['invoice_id'] == invoice_id and
               whole_lira_price_changed(row['current'], row['previous']) for row in comparisons)


def state(today=None, include_analysis=True, db_path=None):
    today = today or datetime.now(ISTANBUL).date()
    with closing(connect(db_path)) as db, db:
        db.execute('BEGIN')
        firms = [dict(r) for r in db.execute('''SELECT f.*,
          COALESCE((SELECT SUM(sample_count) FROM firm_ocr_profiles WHERE firm_id=f.id),0) AS ocr_samples,
          COALESCE((SELECT group_concat(supplier_alias,' · ') FROM firm_ocr_profiles WHERE firm_id=f.id),'') AS ocr_aliases
          FROM firms f WHERE f.merged_into IS NULL ORDER BY name''')]
        invoices = [dict(r) for r in db.execute('''SELECT i.id,i.firm_id,f.name AS firm,i.number,i.identity,i.ettn,i.issued,i.due,i.total,i.import_meta,
          COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) AS paid,
          photo IS NOT NULL AS has_photo FROM invoices i JOIN firms f ON f.id=i.firm_id ORDER BY due,i.id''')]
        firm_payments = [dict(row) for row in db.execute('SELECT * FROM firm_payments')]
        payments = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY paid_on DESC,id DESC')]
        entries = [dict(r) for r in db.execute('''SELECT a.*,f.name AS firm,i.number AS invoice_number,
          COALESCE((SELECT SUM(amount) FROM opening_payments WHERE opening_id=a.id),0) AS paid
          FROM account_entries a JOIN firms f ON f.id=a.firm_id LEFT JOIN invoices i ON i.id=a.invoice_id
          ORDER BY a.entry_date DESC,a.id DESC''')]
        opening_payments = [dict(r) for r in db.execute('SELECT * FROM opening_payments ORDER BY paid_on DESC,id DESC')]
        product_decisions = [dict(r) for r in db.execute('SELECT * FROM product_matches')] if include_analysis else []
        reviews = {r['fingerprint'] for r in db.execute('SELECT fingerprint FROM price_reviews')} if include_analysis else set()
        items = [dict(r) for r in db.execute('''SELECT t.*,i.firm_id,i.issued,i.number,i.identity,f.name AS firm FROM items t
          JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id ORDER BY i.issued,i.id,t.id''')]
    for item in items:
        item['price'] = float(Decimal(item['unit_price']) * 100)
    credits = [entry for entry in entries if entry['kind'] == 'credit']
    openings = [entry for entry in entries if entry['kind'] == 'opening']
    invoice_firms = {entry['id']: entry['firm_id'] for entry in invoices}
    opening_firms = {entry['id']: entry['firm_id'] for entry in openings}
    credit_amounts = {}
    for entry in credits:
        key = entry['invoice_id']
        credit_amounts[key] = credit_amounts.get(key, 0) + entry['amount']
    scheduled = []
    for invoice in invoices:
        invoice['credited'] = credit_amounts.get(invoice['id'], 0)
        invoice['document_remaining'] = invoice['total'] - invoice['paid'] - invoice['credited']
        scheduled.append((invoice, 'invoice'))
    for opening in openings:
        opening['document_remaining'] = opening['amount'] - opening['paid']
        scheduled.append((opening, 'opening'))
    scheduled.sort(key=lambda line: (line[0]['due'], line[0].get('issued', line[0].get('entry_date')), line[1], line[0]['id']))

    available = {}
    for payment in firm_payments:
        key = payment['firm_id']
        available[key] = available.get(key, 0) + payment['amount']
    balances = {key: -amount for key, amount in available.items()}
    totals = dict(today=0, overdue=0, upcoming=0, total=0)
    obligations = []
    for entry, kind in scheduled:
        firm_id = entry['firm_id']
        remaining = entry['document_remaining']
        balances[firm_id] = balances.get(firm_id, 0) + remaining
        entry['remaining'] = remaining_after_firm_payment(firm_id, remaining, available)
        due = date.fromisoformat(entry['due'])
        entry['late_days'] = max(0, (today - due).days) if entry['remaining'] else 0
        if due == today:
            totals['today'] += entry['remaining']
        if due < today:
            totals['overdue'] += entry['remaining']
        if today < due <= today + timedelta(days=7):
            totals['upcoming'] += entry['remaining']
        obligation = dict(entry, kind=kind)
        if kind == 'opening':
            obligation.update(number=entry['reference'] or 'Açılış bakiyesi', issued=entry['entry_date'],
                              total=entry['amount'], has_photo=False)
        obligations.append(obligation)
    totals['total'] = sum(max(0, balance) for balance in balances.values())
    payments = [dict(payment, firm_id=invoice_firms[payment['invoice_id']], target_type='invoice',
                     target_id=payment['invoice_id'], payment_key='invoice:'+str(payment['id'])) for payment in payments]
    payments += [dict(payment, firm_id=opening_firms[payment['opening_id']], invoice_id=None, target_type='opening',
                      target_id=payment['opening_id'], payment_key='opening:'+str(payment['id'])) for payment in opening_payments]
    payments += [dict(payment, invoice_id=None, target_type='firm', target_id=payment['firm_id'],
                      payment_key='firm:'+str(payment['id'])) for payment in firm_payments]
    payments.sort(key=lambda payment:(payment['paid_on'],payment['id'],payment['target_type']), reverse=True)
    paid_items = [item for item in items if not item['gift_only']]
    comparisons,suggestions,unmatched=compare_items(paid_items,product_decisions) if include_analysis else ([], [], [])
    names={product_identity(item):item['product'] for item in paid_items} if product_decisions else {}
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
        if whole_lira_price_changed(item, old):
            fingerprint=hashlib.sha256(json.dumps([comparison['identity'],old['identity'],old['invoice_id'],old['issued'],old['price'],item['identity'],item['invoice_id'],item['issued'],item['price']],ensure_ascii=False).encode()).hexdigest()
            alerts.append(dict(item,fingerprint=fingerprint,history_id=history_id,approved=fingerprint in reviews,old_product=old['product'],matched_by=comparison['matched_by'],old_price=old['price'],change=round((item['price']/old['price']-1)*100,2)))
    price_histories=[]
    for group in price_history_groups.values():
        group['points']=sorted(group['points'].values(),key=lambda point:(point['issued'],point['invoice_id'],point['item_id']))
        if len(group['points'])>1:
            price_histories.append(group)
    price_histories.sort(key=lambda group:(group['firm'],group['product']))
    for firm in firms:
        firm['balance'] = balances.get(firm['id'], 0)
    return dict(today=today.isoformat(), totals=totals, firms=firms, invoices=invoices, openings=openings, credits=credits, obligations=obligations, payments=payments, items=items, alerts=alerts[::-1],price_histories=price_histories,product_suggestions=suggestions,unmatched_products=unmatched,product_match_history=match_history,backup=backup_status())


def audit(db, entity, record_id, before):
    return db.execute("INSERT INTO edits(entity,record_id,changed_on,before_json) VALUES(?,?,?,?)", (entity,record_id,datetime.now(ISTANBUL).isoformat(),json.dumps(before,ensure_ascii=False))).lastrowid


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
        if entity == 'firm' and old['merged_into'] is not None:
            raise ValueError('Bu firma başka bir kartla birleştirildi.')
        if entity == 'firm' and db.execute('SELECT 1 FROM firm_payments WHERE firm_id=?', (record_id,)).fetchone():
            raise ValueError('Cari ödemesi olan firmayı arşivleyin.')
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
        parent = None
        if entity == 'payment' or (entity == 'entry' and old['kind'] == 'credit'):
            parent = db.execute('SELECT identity, firm_id FROM invoices WHERE id=?', (old['invoice_id'],)).fetchone()
        edit_id = audit(db,entity+'_deleted',record_id,dict(record=dict(old),invoices=invoices,payments=payments,items=items,entries=entries,opening_payments=opening_payments,parent=dict(parent) if parent else None))
        for payment in opening_payments:
            db.execute('DELETE FROM opening_payments WHERE id=?',(payment['id'],))
        for entry in entries:
            db.execute('DELETE FROM account_entries WHERE id=?',(entry['id'],))
        for invoice in invoices:
            db.execute('DELETE FROM form_drafts WHERE draft_key IN (?,?)',
                       (f"invoice:{invoice['id']}", f"invoice:uid:{invoice['identity']}"))
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
        return edit_id



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
    ettn = normalize_ettn(data['ettn']) if 'ettn' in data else None
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
        gift_only = t.get('gift_only') is True
        try:
            value = Decimal(str(t['price']))
            if gift_only and value != 0:
                raise ValueError('Bedelsiz satırın birim fiyatı sıfır olmalı.')
            if not gift_only and (not value.is_finite() or value <= 0 or value > 999999999 or value != value.quantize(Decimal('.0001'))):
                raise ValueError('Birim fiyat sıfırdan büyük ve en fazla dört ondalık basamaklı olmalı.')
        except InvalidOperation:
            raise ValueError('Birim fiyat geçersiz.')
        # The original integer price column requires positive cents. The
        # authoritative unit_price stores zero for a bedelsiz row.
        cents = max(1, int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
        quantity = ''
        if 'quantity' in t:
            try:
                quantity_value = Decimal(str(t['quantity']))
                if gift_only and quantity_value != 0:
                    raise ValueError('Bedelsiz satırda ücretli miktar sıfır olmalı.')
                if not gift_only and (not quantity_value.is_finite() or quantity_value <= 0 or quantity_value > 99999999 or quantity_value != quantity_value.quantize(Decimal('.0001'))):
                    raise ValueError('Ürün miktarı sıfırdan büyük ve en fazla dört ondalıklı olmalı.')
                quantity = str(quantity_value)
            except InvalidOperation:
                raise ValueError('Ürün miktarını kontrol edin.')
        gift_quantity = t.get('gift_quantity', '')
        try:
            gift = Decimal('0' if gift_quantity == '' else str(gift_quantity))
            if not gift.is_finite() or not 0 <= gift <= 99999999 or gift != gift.quantize(Decimal('.0001')):
                raise ValueError('Bedelsiz miktar sıfır veya daha büyük ve en fazla dört ondalıklı olmalı.')
        except InvalidOperation:
            raise ValueError('Bedelsiz miktarı kontrol edin.')
        if gift_only and (not gift or not quantity):
            raise ValueError('Bedelsiz satırda bedelsiz miktarı girin.')
        if gift and not quantity:
            raise ValueError('Bedelsiz miktar girildiğinde ücretli miktarı da belirtin.')
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
        if gift_only:
            try:
                if any(Decimal(field or '0') != 0 for field in (*source, line_total)):
                    raise ValueError('Bedelsiz satırın tutarı ve KDV tutarı sıfır olmalı.')
            except InvalidOperation:
                raise ValueError('Bedelsiz satırın tutarını kontrol edin.')
        rows.append((clean(t['product'], 'Ürün', 500), clean(t['unit'], 'Ambalaj / fiyat birimi'), cents, str(value), quantity, code, *source,row_number,line_total,format(gift, 'f'),int(gift_only)))
    paid_rows = [row for row in rows if not row[-1]]
    if len({(p.casefold(), u.casefold()) for p,u,*_ in paid_rows}) != len(paid_rows):
        raise ValueError('Aynı ürün ve birimi faturada bir kez girin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM invoices WHERE id=?', (record_id,)).fetchone() if record_id is not None else None
        if record_id is not None and not old:
            raise ValueError('Fatura bulunamadı.')
        if old and 'invoice_identity' in data and data['invoice_identity'] != old['identity']:
            raise ValueError('Bu fatura artık aynı kayıt değil. Listeyi yenileyip faturayı yeniden açın.')
        if ettn is None:
            ettn = old['ettn'] if old else ''
        if ettn:
            duplicate = db.execute('SELECT number FROM invoices WHERE ettn=? AND id!=?',
                                   (ettn, record_id or -1)).fetchone()
            if duplicate:
                raise ValueError(f"Bu ETTN, {duplicate['number']} numaralı faturada zaten kayıtlı.")
        firm = db.execute('SELECT archived FROM firms WHERE id=?', (firm_id,)).fetchone()
        if not firm:
            raise ValueError('Firma bulunamadı.')
        if firm['archived'] and not db.execute('SELECT 1 FROM invoices WHERE id=? AND firm_id=?',
                                               (record_id, firm_id)).fetchone():
            raise ValueError('Yeni fatura eklemek için firmayı arşivden çıkarın.')
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
        db.execute('UPDATE invoices SET extra_photos=?,import_meta=?,source_hash=?,ettn=? WHERE id=?',
                   (json.dumps(photos[1:]),json.dumps(import_metadata or {}),source_hash,ettn,record_id))
        db.executemany('INSERT INTO items(invoice_id,product,unit,price,unit_price,quantity,code,source_net,source_vat,row_number,line_total,gift_quantity,gift_only) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [(record_id,*r) for r in rows])
        supplier_alias = str(data.get('ocr_supplier') or '').strip()[:200]
        if supplier_alias:
            db.execute('''INSERT INTO firm_ocr_profiles(firm_id,supplier_alias,sample_count,last_seen) VALUES(?,?,1,?)
              ON CONFLICT(firm_id,supplier_alias) DO UPDATE SET sample_count=sample_count+1,last_seen=excluded.last_seen''',
              (firm_id,supplier_alias,datetime.now(ISTANBUL).isoformat()))
    return record_id


def firm_balance(db, firm_id):
    row = db.execute('''SELECT
        COALESCE((SELECT SUM(total) FROM invoices WHERE firm_id=firms.id),0)
        + COALESCE((SELECT SUM(CASE kind WHEN 'opening' THEN amount ELSE -amount END)
                    FROM account_entries WHERE firm_id=firms.id),0)
        - COALESCE((SELECT SUM(p.amount) FROM payments p JOIN invoices i ON i.id=p.invoice_id
                    WHERE i.firm_id=firms.id),0)
        - COALESCE((SELECT SUM(p.amount) FROM opening_payments p JOIN account_entries a ON a.id=p.opening_id
                    WHERE a.firm_id=firms.id),0)
        - COALESCE((SELECT SUM(amount) FROM firm_payments WHERE firm_id=firms.id),0)
        FROM firms WHERE id=? AND merged_into IS NULL''', (firm_id,)).fetchone()
    if row is None:
        raise ValueError('Firma bulunamadı.')
    return row[0]


def firm_merge_details(db, source, target):
    if source == target:
        raise ValueError('İki farklı firma seçin.')
    firms = {}
    for firm_id in (source, target):
        row = db.execute('SELECT id,name,archived,default_due_days,ocr_amount_order,phone,tax_number FROM firms '
                         'WHERE id=? AND merged_into IS NULL', (firm_id,)).fetchone()
        if not row:
            raise ValueError('Firma bulunamadı veya zaten birleştirildi.')
        firms[firm_id] = dict(row, balance=firm_balance(db, firm_id))
    counts = db.execute('''SELECT
        (SELECT COUNT(*) FROM invoices WHERE firm_id=:source) AS invoices,
        (SELECT COUNT(*) FROM account_entries WHERE firm_id=:source AND kind='opening') AS openings,
        (SELECT COUNT(*) FROM account_entries WHERE firm_id=:source AND kind='credit') AS credits,
        (SELECT COUNT(*) FROM firm_payments WHERE firm_id=:source)
        + (SELECT COUNT(*) FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE i.firm_id=:source)
        + (SELECT COUNT(*) FROM opening_payments p JOIN account_entries a ON a.id=p.opening_id
           WHERE a.firm_id=:source) AS payments''', {'source':source}).fetchone()
    duplicates = [row['number'] for row in db.execute('''SELECT old.number FROM invoices old
        JOIN invoices keep ON keep.number=old.number WHERE old.firm_id=? AND keep.firm_id=?
        ORDER BY old.number''', (source, target))]
    details = dict(source=firms[source], target=firms[target],
                   balance=firms[source]['balance']+firms[target]['balance'],
                   counts=dict(counts), duplicates=duplicates)
    details['token'] = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
    return details


def move_firm_product_matches(db, source, target):
    reviews = db.execute('SELECT * FROM product_matches ORDER BY decided_on').fetchall()
    moved = {}
    for row in reviews:
        left, right = (json.loads(row[field]) for field in ('left_key', 'right_key'))
        if source not in (left[0], right[0]):
            moved[row['fingerprint']] = tuple(row)
    for row in reviews:
        left, right = (json.loads(row[field]) for field in ('left_key', 'right_key'))
        if source not in (left[0], right[0]):
            continue
        for key in (left, right):
            if key[0] == source:
                key[0] = target
        left, right = (json.dumps(key, ensure_ascii=False) for key in (left, right))
        fingerprint = pair_key(left, right)
        if left == right:
            if row['decision'] == 'different':
                raise ValueError('Birleştirme bir ürün eşleştirme kararıyla çakışıyor.')
            continue
        previous = moved.get(fingerprint)
        if previous and previous[3] != row['decision']:
            raise ValueError('Firmaların ürün eşleştirme kararları çakışıyor. Kararları kontrol edin.')
        moved[fingerprint] = (fingerprint,left,right,row['decision'],row['decided_on'])
    db.execute('DELETE FROM product_matches')
    db.executemany('INSERT INTO product_matches VALUES(?,?,?,?,?)', moved.values())


def merge_firms(source, target, token):
    with closing(connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        details = firm_merge_details(db, source, target)
        if details['duplicates']:
            raise ValueError('Çakışan fatura numaralarını kontrol edin.')
        source_tax, target_tax = details['source']['tax_number'], details['target']['tax_number']
        if source_tax and target_tax and source_tax != target_tax:
            raise ValueError('Vergi numaraları farklı olan firmalar birleştirilemez.')
        if token != details['token']:
            raise ValueError('Firma bilgileri değişti. Önizlemeyi yenileyin.')
        automatic_backup()
        move_firm_product_matches(db, source, target)
        for table in ('invoices', 'account_entries', 'firm_payments'):
            db.execute(f'UPDATE {table} SET firm_id=? WHERE firm_id=?', (target, source))
        db.execute('''INSERT INTO firm_ocr_profiles(firm_id,supplier_alias,sample_count,last_seen)
            SELECT ?,supplier_alias,sample_count,last_seen FROM firm_ocr_profiles WHERE firm_id=?
            ON CONFLICT(firm_id,supplier_alias) DO UPDATE SET
              sample_count=firm_ocr_profiles.sample_count+excluded.sample_count,
              last_seen=MAX(firm_ocr_profiles.last_seen,excluded.last_seen)''', (target, source))
        db.execute('DELETE FROM firm_ocr_profiles WHERE firm_id=?', (source,))
        db.execute('INSERT OR IGNORE INTO firm_ocr_profiles VALUES(?,?,0,?)',
                   (target,details['source']['name'],datetime.now(ISTANBUL).isoformat()))
        for row in db.execute('SELECT draft_key,payload FROM form_drafts').fetchall():
            payload = json.loads(row['payload'])
            changed = False
            fields = payload.get('fields')
            for field in fields if isinstance(fields, list) else ():
                if isinstance(field, dict) and field.get('name') == 'firm_id' and str(field.get('value')) == str(source):
                    field['value'] = str(target)
                    changed = True
            if str(payload.get('layoutFirm')) == str(source):
                payload['layoutFirm'] = str(target)
                changed = True
            if changed:
                db.execute('UPDATE form_drafts SET payload=? WHERE draft_key=?',
                           (json.dumps(payload, ensure_ascii=False),row['draft_key']))
        if not details['target']['phone'] and details['source']['phone']:
            db.execute('UPDATE firms SET phone=? WHERE id=?', (details['source']['phone'], target))
        db.execute('UPDATE firms SET archived=1,merged_into=? WHERE id=?', (target, source))
        if source_tax and not target_tax:
            db.execute('UPDATE firms SET tax_number=? WHERE id=?', (source_tax, target))
        audit(db,'firm_merged',source,details)


def create_firm_payment(data, record_id=None):
    firm_id = int(data['firm_id'])
    amount = money(data['amount'])
    paid_on = date.fromisoformat(data['paid_on'])
    if paid_on > datetime.now(ISTANBUL).date():
        raise ValueError('Ödeme tarihi gelecekte olamaz.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        limit = firm_balance(db, firm_id)
        if record_id is not None:
            old = db.execute('SELECT * FROM firm_payments WHERE id=?', (record_id,)).fetchone()
            if not old:
                raise ValueError('Ödeme bulunamadı.')
            if old['firm_id'] == firm_id:
                limit = max(old['amount'], limit + old['amount'])
        if amount > limit:
            raise ValueError('Ödeme firmanın kalan cari borcunu aşamaz.')
        values = (firm_id, amount, paid_on.isoformat())
        if record_id is None:
            db.execute('INSERT INTO firm_payments(firm_id,amount,paid_on) VALUES(?,?,?)', values)
        else:
            audit(db, 'firm_payment', record_id, dict(old))
            db.execute('UPDATE firm_payments SET firm_id=?,amount=?,paid_on=? WHERE id=?', (*values, record_id))


def create_payment(data, record_id=None):
    if data.get('target_type') == 'firm' or ('firm_id' in data and 'invoice_id' not in data and not data.get('target_type')):
        return create_firm_payment(data, record_id)
    if data.get('target_type') == 'opening':
        return create_opening_payment(data, record_id)
    amount = money(data['amount'])
    paid_on = date.fromisoformat(data['paid_on'])
    if paid_on > datetime.now(ISTANBUL).date():
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
        if kind == 'opening' and (not old or old['firm_id'] != firm_id):
            firm = db.execute('SELECT archived FROM firms WHERE id=?', (firm_id,)).fetchone()
            if not firm or firm['archived']:
                raise ValueError('Açılış bakiyesi için etkin bir firma seçin.')
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
    if paid_on > datetime.now(ISTANBUL).date():
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
    kind = data.get('target_type')
    if kind not in ('opening', 'firm'):
        return delete_record('payment', int(data['id']))
    table = 'opening_payments' if kind == 'opening' else 'firm_payments'
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record_id = int(data['id'])
        old = db.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
        if not old:
            raise ValueError('Ödeme bulunamadı.')
        parent = None
        if kind == 'opening':
            parent = dict(db.execute('SELECT * FROM account_entries WHERE id=?', (old['opening_id'],)).fetchone())
        edit_id = audit(db, kind+'_payment_deleted', record_id, dict(record=dict(old), parent=parent))
        db.execute(f'DELETE FROM {table} WHERE id=?', (record_id,))
        return edit_id


def restore_deleted_record(edit_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        edit = db.execute('''SELECT entity,before_json FROM edits WHERE id=?
            AND NOT EXISTS (SELECT 1 FROM restored_deletions WHERE edit_id=edits.id)''', (edit_id,)).fetchone()
        tables = {'invoice_deleted':'invoices', 'entry_deleted':'account_entries',
                  'payment_deleted':'payments', 'opening_payment_deleted':'opening_payments',
                  'firm_payment_deleted':'firm_payments'}
        if not edit or edit['entity'] not in tables:
            raise ValueError('Geri alınabilecek silme işlemi bulunamadı.')
        snapshot = json.loads(edit['before_json'])
        record = snapshot.get('record')
        if not record:
            raise ValueError('Bu silme işlemi geri alınamıyor.')
        if record.get('firm_id') and db.execute('SELECT 1 FROM firms WHERE id=? AND merged_into IS NOT NULL',
                                               (record['firm_id'],)).fetchone():
            raise ValueError('Bu kayıt silindikten sonra firma başka bir kartla birleştirildi.')
        table = tables[edit['entity']]
        if table == 'invoices' and record.get('ettn') and db.execute(
            'SELECT 1 FROM invoices WHERE ettn=?', (record['ettn'],)
        ).fetchone():
            raise ValueError('Bu ETTN başka bir faturada kayıtlı. Silme işlemi geri alınamadı.')
        if table == 'invoices' and record['source_hash'] and db.execute(
            'SELECT 1 FROM invoices WHERE source_hash=?', (record['source_hash'],)
        ).fetchone():
            raise ValueError('Bu fatura dosyası yeniden kaydedilmiş.')
        if table in ('payments', 'account_entries') and record.get('invoice_id') is not None:
            target = db.execute('''SELECT identity,firm_id,total
                - COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=invoices.id),0)
                - COALESCE((SELECT SUM(amount) FROM account_entries WHERE invoice_id=invoices.id),0) AS remaining
                FROM invoices WHERE id=?''', (record['invoice_id'],)).fetchone()
            if (not target or snapshot.get('parent') != {'identity':target['identity'], 'firm_id':target['firm_id']}
                    or record['amount'] > target['remaining']):
                raise ValueError('Bağlı fatura veya kalan borç değişti. İşlem geri alınamadı.')
        if table == 'firm_payments' and record['amount'] > firm_balance(db, record['firm_id']):
            raise ValueError('Firmanın kalan cari borcu değişti. Ödeme geri alınamadı.')
        if table == 'opening_payments':
            target = db.execute('SELECT * FROM account_entries WHERE id=?', (record['opening_id'],)).fetchone()
            paid = db.execute('SELECT COALESCE(SUM(amount),0) FROM opening_payments WHERE opening_id=?',
                              (record['opening_id'],)).fetchone()[0]
            if not target or dict(target) != snapshot.get('parent') or paid + record['amount'] > target['amount']:
                raise ValueError('Açılış bakiyesi değişti. Ödeme geri alınamadı.')

        restore_rows = [(table, [record])]
        if table == 'invoices':
            restore_rows.extend([('items', snapshot['items']), ('payments', snapshot['payments']),
                                 ('account_entries', snapshot['entries'])])
        if table == 'account_entries':
            restore_rows.append(('opening_payments', snapshot['opening_payments']))
        try:
            for target_table, rows in restore_rows:
                columns = [row['name'] for row in db.execute(f'PRAGMA table_info({target_table})')]
                for row in rows:
                    fields = [column for column in columns if column in row]
                    names = ','.join(fields)
                    placeholders = ','.join('?' for _ in fields)
                    db.execute(f'INSERT INTO {target_table} ({names}) VALUES ({placeholders})',
                               [row[field] for field in fields])
        except sqlite3.IntegrityError as error:
            raise ValueError('Kayıt veya bağlı bilgiler değişti. Silme işlemi geri alınamadı.') from error
        db.execute('INSERT INTO restored_deletions VALUES(?)', (edit_id,))


class Handler(BaseHTTPRequestHandler):
    confirm_invoice = None

    def log_message(self, format, *args):
        # PyInstaller's windowed Windows executable intentionally has no
        # stderr stream. BaseHTTPRequestHandler otherwise crashes while
        # logging every successful response, before sending it to the browser.
        if sys.stderr is not None:
            super().log_message(format, *args)

    def respond(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.path, _, query = self.path.partition("?")
        if self.path == '/api/state':
            return self.respond(dict(state(), desktop_invoice_confirmation=self.confirm_invoice is not None))
        if self.path == '/api/reports':
            params = {key: values[0] for key, values in parse_qs(query).items()}
            try:
                with closing(connect()) as db, db:
                    db.execute('BEGIN')
                    report = range_report(db, params.get('start', ''), params.get('end', ''),
                                          params.get('previous_start'), params.get('previous_end'))
                return self.respond(report)
            except ValueError:
                return self.respond({'error':'Geçerli bir rapor tarih aralığı seçin.'},400)
        if self.path == '/api/overview':
            return self.respond(dict(state(include_analysis=False), desktop_invoice_confirmation=self.confirm_invoice is not None))
        if self.path.startswith('/api/invoice-price-count/'):
            try:
                invoice_id = int(self.path.rsplit('/', 1)[1])
                return self.respond({'count':invoice_price_change_count(invoice_id)})
            except ValueError:
                return self.respond({'error':'Fatura bulunamadı.'},404)
        if self.path.startswith('/api/monthly-purchases/'):
            try:
                return self.respond(monthly_purchases(self.path.rsplit('/', 1)[1]))
            except ValueError:
                return self.respond({'error':'Geçerli bir alış ayı seçin.'},400)
        if self.path == '/api/supplier-comparisons':
            with closing(connect()) as db:
                items = [dict(row) for row in db.execute('''SELECT t.id, t.invoice_id, t.product, t.code,
                    t.unit, t.unit_price, i.issued, i.number, f.id AS firm_id, f.name AS firm, f.archived
                    FROM items t JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id
                    WHERE t.gift_only=0''')]
            return self.respond({'groups': compare_suppliers(items)})
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
            if self.path == '/api/invoice-confirmation' and self.confirm_invoice is not None:
                total = money(data['total'])
                summary = (clean(data['firm'], 'Firma adı'), clean(data['number'], 'Fatura numarası'),
                           f'{Decimal(total)/100:.2f}'.replace('.', ',') + ' TL')
                return self.respond({'confirmed': self.confirm_invoice(summary)})
            if self.path == '/api/firms':
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    firm = dict(name=clean(data['name'], 'Firma adı'),
                                default_due_days=default_due_days(data.get('default_due_days')),
                                phone=firm_phone(data.get('phone', '')),
                                tax_number=firm_tax_number(db, data.get('tax_number', '')))
                    firm['id'] = db.execute('INSERT INTO firms(name,default_due_days,phone,tax_number) '
                                            'VALUES(:name,:default_due_days,:phone,:tax_number)', firm).lastrowid
                return self.respond({'ok':True,'firm':dict(firm, archived=0, ocr_amount_order='', ocr_samples=0)},201)
            elif self.path == '/api/invoices':
                return self.respond({'ok':True,'invoice_id':create_invoice(data)},201)
            elif self.path == '/api/payments':
                create_payment(data)
            elif self.path == '/api/invoices/edit':
                return self.respond({'ok':True,'invoice_id':create_invoice(data, int(data['id']))},201)
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
                return self.respond({'undo_id':delete_record('entry',int(data['id']))},201)
            elif self.path == '/api/firms/delete':
                delete_record('firm',int(data['id']),data.get('expected'))
            elif self.path == '/api/invoices/delete':
                return self.respond({'undo_id':delete_record('invoice',int(data['id']),data.get('expected'))},201)
            elif self.path == '/api/payments/delete':
                return self.respond({'undo_id':delete_payment(data)},201)
            elif self.path == '/api/deletions/restore':
                restore_deleted_record(int(data['id']))
            elif self.path == '/api/firms/ocr-layout':
                order = data['order']
                if order not in ('', 'vat_net', 'net_vat'):
                    raise ValueError('Geçerli bir fatura sütun düzeni seçin.')
                with connect() as db:
                    if not db.execute('UPDATE firms SET ocr_amount_order=? WHERE id=? AND merged_into IS NULL',
                                      (order, int(data['id']))).rowcount:
                        raise ValueError('Firma bulunamadı.')
            elif self.path == '/api/firms/merge-preview':
                with closing(connect()) as db, db:
                    db.execute('BEGIN')
                    return self.respond(firm_merge_details(db, int(data['source']), int(data['target'])))
            elif self.path == '/api/firms/merge':
                merge_firms(int(data['source']), int(data['target']), data['token'])
            elif self.path == '/api/firms/archive':
                if not isinstance(data['archived'], bool):
                    raise ValueError('Geçerli bir arşiv işlemi seçin.')
                with connect() as db:
                    if not db.execute('UPDATE firms SET archived=? WHERE id=? AND merged_into IS NULL',
                                      (data['archived'],int(data['id']))).rowcount:
                        raise ValueError('Firma bulunamadı.')
            elif self.path == '/api/backups/restore':
                restore_backup(data.get('name'))
            elif self.path == '/api/backups/create':
                automatic_backup()
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
                    old = db.execute('SELECT * FROM firms WHERE id=? AND merged_into IS NULL',(int(data['id']),)).fetchone()
                    if not old:
                        raise ValueError('Firma bulunamadı.')
                    audit(db,'firm',int(data['id']),dict(old))
                    days = default_due_days(data.get('default_due_days', old['default_due_days']))
                    phone = firm_phone(data.get('phone', old['phone']))
                    tax_number = firm_tax_number(db, data.get('tax_number', old['tax_number']), old['id'])
                    db.execute('UPDATE firms SET name=?,default_due_days=?,phone=?,tax_number=? WHERE id=?',
                               (clean(data['name'],'Firma adı'), days, phone, tax_number, old['id']))
            elif self.path == '/api/products/package-comparison':
                ids = data['item_ids']
                if (not isinstance(ids, list) or len(ids)!=2 or any(type(item_id) is not int for item_id in ids)
                        or ids[0]==ids[1]):
                    raise ValueError('Karşılaştırmak için iki farklı alış satırı seçin.')
                with closing(connect()) as db:
                    items = [dict(row) for row in db.execute('''SELECT t.id,t.invoice_id,t.product,t.unit,
                        t.unit_price,i.issued,i.number,f.name AS firm FROM items t
                        JOIN invoices i ON i.id=t.invoice_id JOIN firms f ON f.id=i.firm_id
                        WHERE t.id IN (?,?) AND t.gift_only=0''', ids)]
                if len(items)!=2:
                    raise ValueError('Seçilen alış satırlarından biri bulunamadı. Seçimi yenileyin.')
                return self.respond(compare_package_prices(items))
            elif self.path == '/api/products/unmatch':
                with connect() as db:
                    db.execute('DELETE FROM product_matches WHERE fingerprint=?',(data['fingerprint'],))
            elif self.path == '/api/products/match':
                suggestion=next((s for s in state()['product_suggestions'] if s['fingerprint']==data['fingerprint']),None)
                if not suggestion or data.get('decision') not in ('same','different'):
                    raise ValueError('Eşleştirme güncellenmiş veya karar geçersiz. Listeyi yenileyin.')
                with connect() as db:
                    db.execute('INSERT OR REPLACE INTO product_matches VALUES(?,?,?,?,?)',(suggestion['fingerprint'],suggestion['left_key'],suggestion['right_key'],data['decision'],datetime.now(ISTANBUL).isoformat()))
            elif self.path == '/api/prices/approve':
                fingerprint = data['fingerprint']
                if fingerprint not in {a['fingerprint'] for a in state()['alerts']}:
                    raise ValueError('Fiyat değişimi güncellenmiş. Listeyi yenileyip tekrar kontrol edin.')
                with connect() as db:
                    db.execute('INSERT OR IGNORE INTO price_reviews VALUES(?,?)',(fingerprint,datetime.now(ISTANBUL).isoformat()))
            elif self.path == '/api/ocr':
                photos = data.get('photos') or ([data['photo']] if data.get('photo') else [])
                if not isinstance(photos,list) or not 1 <= len(photos) <= 6:
                    raise ValueError('1 ile 6 arasında fotoğraf seçin.')
                photos = [photo(p) for p in photos]
                if not all(photos):
                    raise ValueError('Fotoğraf verisi geçersiz.')
                with connect() as db:
                    firms = [dict(r) for r in db.execute('''SELECT id,name,tax_number FROM firms WHERE merged_into IS NULL AND archived=0
                      UNION ALL SELECT f.id,p.supplier_alias,f.tax_number FROM firm_ocr_profiles p
                      JOIN firms f ON f.id=p.firm_id WHERE f.merged_into IS NULL AND f.archived=0''')]
                    layouts = {row['id']: (row['name'], row['ocr_amount_order']) for row in db.execute(
                        "SELECT id,name,ocr_amount_order FROM firms WHERE merged_into IS NULL AND ocr_amount_order != ''"
                    )}
                selected_firm = int(data['layout_firm_id']) if data.get('layout_firm_id') else None
                readings, visible_text, coordinates, pdf_coordinates = [], [], [], []
                pdf_layers = data.get('pdf_text_layers') or []
                pdf_readings, pdf_text = pdf_text_readings(pdf_layers, firms, pdf_coordinates)
                if pdf_readings:
                    readings.extend(pdf_readings)
                    visible_text.append('--- PDF içindeki orijinal metin ---\n'+pdf_text)
                    if (not selected_firm and data.get('source_page_count') == len(pdf_layers)
                            and all(layer['words'] for layer in pdf_layers)):
                        draft = verified_pdf_draft(pdf_readings)
                        if draft:
                            return self.respond({'text': visible_text[0], 'language': 'PDF', 'draft': draft})

                photos = ocr_photo_variants(photos)
                executable, lang = ocr_engine()

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
                            coordinates.append(page_tsv)
                            positioned = tsv_reading(page_tsv)
                            # When the plain OCR already accounts for every row
                            # and reconciles exactly to the printed total, keep
                            # that complete reading. A partial coordinate pass
                            # must not replace one correct row and then make the
                            # otherwise complete invoice look incomplete.
                            plain_complete = bool(
                                plain.get('total')
                                and plain.get('items')
                                and not check_items(
                                    plain['items'], plain['total'], plain.get('import_check', {})
                                )['errors']
                            )
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
                            positioned_rows = set() if plain_complete else {item['row_number'] for item in positioned['items']}
                            # Keep plain-text rows only when the coordinate reading
                            # did not already reconstruct that invoice row.
                            plain['items'] = [
                                item for item in plain['items']
                                if item['row_number'] not in positioned_rows
                            ]
                            if not plain_complete:
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
                draft = apply_supplier_layout(draft, coordinates, layouts, selected_firm, pdf_coordinates)
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
        except OSError:
            self.respond({'error':'Yedek konumuna erişilemedi. Klasörü kontrol edin.'},503)


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
    with ThreadingHTTPServer(('127.0.0.1',port),Handler) as server:
        stop_backup = start_periodic_backup()
        try:
            server.serve_forever()
        finally:
            stop_backup.set()
