"""Offline Turkish invoice text parser. Produces reviewable drafts, never saves."""
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalized(text):
    text = text.replace('ı', 'i').replace('İ', 'I')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()


AMOUNT = r'\d+(?:[. ]\d{3})*,\d{2,4}|\d+\.\d{2,4}'


def decimal_value(text):
    text = text.strip().replace(' ', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    return Decimal(text)


def parse_invoice(text, firms=()):
    lines = [re.sub(r'[|\t]+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    result = dict(number='', issued='', total='', supplier='', firm_id=None, items=[], warnings=[])
    for index, line in enumerate(lines):
        label = normalized(line)
        if re.search(r'(fatura|belge)\s*(no|numara)', label):
            match = re.search(r'\b[A-Z0-9]{3}20\d{2}\d{9}\b', line.upper())
            if match:
                result['number'] = match.group()
        if 'fatura tarih' in label:
            match = re.search(r'(\d{2})\s*[.\-/]\s*(\d{2})\s*[.\-/]\s*(\d{4})', line)
            if match:
                try:
                    day, month, year = map(int, match.groups())
                    result['issued'] = date(year, month, day).isoformat()
                except ValueError:
                    pass
        if re.search(r'odenecek\s*(tutar|toplam)|genel\s*toplam|vergiler\s*dahil\s*toplam', label):
            amounts = re.findall(AMOUNT, line)
            if amounts:
                value = decimal_value(amounts[-1])
                if value > 0:
                    result['total'] = str(value.quantize(Decimal('.01')))
        if not result['supplier'] and re.search(r'gida|pazarlama|mesrubat|ticaret|ltd|sanayi', label) and index < 25:
            result['supplier'] = line[:200]
    # Only exact distinctive-name matches; never silently select an unrelated supplier.
    supplier = normalized(result['supplier'])
    generic = {'gida', 've', 'ltd', 'sti', 'san', 'tic', 'paz', 'market', 'meşrubat', 'mesrubat', 'eti'}
    matches = []
    for firm in firms:
        tokens = [w for w in re.findall(r'\w+', normalized(firm['name'])) if len(w) > 3 and w not in generic]
        if tokens and all(re.search(r'\b' + re.escape(w) + r'\b', supplier) for w in tokens):
            matches.append(firm['id'])
    if len(matches) == 1:
        result['firm_id'] = matches[0]
    else:
        result['warnings'].append('Firma eşleştirilemedi. Faturanın ait olduğu firmayı seçin.')
    # Join product-name/price wraps until the next row or totals section.
    blocks, current = [], ''
    for line in lines:
        if re.match(r'^\d{1,3}\s+\d{5,14}\b', line):
            if current:
                blocks.append(current)
            current = line
        elif re.search(r'odenecek|toplam|iskonto|etigenisk|vergi muafiyet', normalized(line)):
            if current:
                blocks.append(current)
                current = ''
        elif current:
            current += ' ' + line
    if current:
        blocks.append(current)
    seen = set()
    for block in blocks:
        match = re.match(r'^\d{1,3}\s+(\d{5,14})\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s+(Adet|Ad\.?|Kg|Koli|Lt|Litre)\s+(.+)', block, re.I)
        if not match:
            result['warnings'].append('Bir ürün satırı okunamadı; fotoğrafla karşılaştırın.')
            continue
        code, product, quantity, unit, tail = match.groups()
        amounts = re.findall(AMOUNT, tail)
        try:
            qty = Decimal(quantity.replace(',', '.'))
            if qty <= 0 or len(amounts) < 4:
                raise ValueError()
            # Supported table ends: unit price, VAT %, VAT amount, line net amount.
            vat, net = map(decimal_value, amounts[-2:])
            raw_price = decimal_value(amounts[0])
            if vat < 0 or net <= 0 or raw_price <= 0:
                raise ValueError()
            price = ((net + vat) / qty).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
            signature = (block.split()[0], code, product, str(qty), str(net), str(vat))
            if signature in seen:
                continue
            seen.add(signature)
            result['items'].append(dict(product=product[:200], unit=unit.lower(), price=str(price), code=code,
                                        quantity=str(qty), source_price=str(raw_price), net=str(net), vat=str(vat)))
        except (InvalidOperation, ValueError):
            result['warnings'].append('Bir ürünün son birim fiyatı hesaplanamadı; elle kontrol edin.')
    for key, label in [('number','Fatura numarası'), ('issued','Fatura tarihi'), ('total','Ödenecek toplam')]:
        if not result[key]:
            result['warnings'].append(label + ' okunamadı; elle girin.')
    if not result['items']:
        result['warnings'].append('Ürün tablosu ayrıştırılamadı. Daha net fotoğraf deneyin veya ürünleri elle girin.')
    elif result['total']:
        rows_total = sum(Decimal(i['net']) + Decimal(i['vat']) for i in result['items'])
        if abs(rows_total - Decimal(result['total'])) > Decimal('.05'):
            result['warnings'].append('Okunan ürün satırlarının toplamı fatura toplamıyla uyuşmuyor. Eksik sayfa, farklı masraf veya hatalı okuma olabilir; ürünleri kontrol edin.')
    result['warnings'].append('Vade otomatik tahmin edilmez. Vade gününü girin ve tüm alanları fotoğrafla karşılaştırın.')
    return result
