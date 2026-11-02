"""Offline Turkish invoice text parser. Produces reviewable drafts, never saves."""
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalized(text):
    text = text.replace('ı', 'i').replace('İ', 'I')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()


AMOUNT = r'(?<![\w.,])(?:\d{1,3}(?:[. ]\d{3})+[,.]\d{2,4}|\d+[,.]\d{2,4})(?![\d.,])'
DATE = r'(?<!\d)(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(20\d{2})(?!\d)'


def decimal_value(text):
    text = re.sub(r'\s+', '', text)
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    elif text.count('.') > 1:
        head, tail = text.rsplit('.',1)
        text = head.replace('.','') + '.' + tail
    return Decimal(text)


def parse_date(text):
    match = re.search(DATE, text)
    if match:
        try:
            day, month, year = map(int, match.groups())
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    return ''


def label_kind(line):
    label = normalized(line)
    compact = re.sub(r'[^a-z]', '', label)
    if re.search(r'(fatura|belge)\s*(no|numara)',label) or any(x in compact for x in ('faturano','belgeno')):
        return 'number'
    if 'faturatarih' in compact or 'duzenlemetarih' in compact:
        return 'issued'
    for canonical in ('faturatarihi','belgeno','faturano','odenecektutar','vergilerdahiltoplamtutar','geneltoplam'):
        # Compare label prefixes, not whole paragraphs or arbitrary monetary rows.
        if compact and SequenceMatcher(None,compact[:len(canonical)+1],canonical).ratio() >= .88:
            return {'faturatarihi':'issued','belgeno':'number','faturano':'number'}.get(canonical,'total')
    if any(x in compact for x in ('odenecektutar','odenecektoplam','vergilerdahiltoplam','geneltoplam')):
        return 'total'
    return None


def invoice_number(text):
    # Rejoin spaces inside the standard 16-character e-invoice ID without inventing digits.
    standard = re.search(r'(?<![A-Z0-9])([A-Z0-9]{3})\s*(20\d{2})\s*(\d{9})(?!\d)',text.upper())
    if standard:
        return ''.join(standard.groups())
    # Other invoice IDs are accepted only after an explicit label.
    tail = re.sub(r'^.*?(?:fatura|belge)\s*(?:numarasi|numara|no)\s*[:.\-]*','',text,flags=re.I).strip()
    if len(tail) <= 40:
        tokens = re.findall(r'(?<!\w)[A-Z0-9][A-Z0-9/\-]{4,39}(?!\w)',tail.upper())
        tokens = [x for x in tokens if re.search(r'\d',x) and not re.fullmatch(r'\d{2,4}-\d{2}-\d{2,4}',x) and not re.fullmatch(r'[A-Z0-9]{3}20\d{2}\d*',x)]
        if len(tokens)==1:
            return tokens[0]
    return ''


def parse_invoice(text, firms=()):
    lines = [re.sub(r'[|\t]+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    lines = [re.sub(r'(?<=\d)([.,])\s+(?=\d)',r'\1',line) for line in lines]
    result = dict(number='', issued='', total='', supplier='', firm_id=None, items=[], warnings=[], sources={})
    candidates = dict(number=[],issued=[],total=[])
    for index, line in enumerate(lines):
        label = normalized(line)
        kind = label_kind(line)
        if kind:
            window = [line]
            for following in lines[index+1:index+3]:
                if label_kind(following) or re.search(r'toplam|iskonto|kdv|vkn|tckn|irsaliye',normalized(following)):
                    break
                window.append(following)
            for offset,value_line in enumerate(window):
                value=''
                if kind=='number':
                    value=invoice_number(value_line)
                elif kind=='issued':
                    value=parse_date(value_line)
                else:
                    amounts=re.findall(AMOUNT,value_line)
                    if not amounts:
                        integer=re.search(r'(?<![\d.,])(\d+)\s*(?:TL|TRY|₺)\b',value_line,re.I)
                        amounts=[integer[1]] if integer else []
                    if amounts:
                        amount=decimal_value(amounts[-1])
                        if amount>0:
                            value=str(amount.quantize(Decimal('.01')))
                if value:
                    candidates[kind].append((value,' '.join(window[:offset+1])))
                    break
        if not result['supplier'] and re.search(r'gida|pazarlama|mesrubat|ticaret|ltd|sanayi', label) and index < 40:
            result['supplier'] = line[:200]
    for key,values in candidates.items():
        unique={value for value,_ in values}
        if len(unique)==1:
            result[key]=values[0][0]
            result['sources'][key]=values[0][1]
        elif len(unique)>1:
            result['warnings'].append('Birden fazla '+{'number':'fatura numarası','issued':'fatura tarihi','total':'ödenecek toplam'}[key]+' okundu; doğru bilgiyi seçin. Farklı faturaları birlikte eklemeyin.')
    # An unlabeled date or standard invoice ID is only offered when unambiguous.
    if not candidates['number']:
        numbers={m.group() for m in re.finditer(r'\b[A-Z0-9]{3}20\d{2}\d{9}\b',text.upper())}
        if len(numbers)==1:
            result['number']=next(iter(numbers));result['sources']['number']=result['number']
    if not candidates['issued']:
        dates={parse_date(line) for line in lines if not re.search(r'irsaliye|vade|odeme|sevk|teslim',normalized(line))}
        dates.discard('')
        if len(dates)==1:
            result['issued']=next(iter(dates));result['sources']['issued']=result['issued']
            result['warnings'].append('Tarih tek tarih adayı olarak bulundu; fatura tarihi olduğunu kontrol edin.')
    supplier = normalized(result['supplier'])
    supplier_words = re.findall(r'[a-z0-9]+',supplier)
    generic = {'gida','ltd','sti','san','tic','paz','market','mesrubat','eti','biskuvi','dagitim','ticaret','sanayi','pazarlama','anonim','sirketi','merkez','toptan'}
    matches=[]
    for firm in firms:
        words=[w for w in re.findall(r'[a-z0-9]+',normalized(firm['name'])) if len(w)>3 and w not in generic]
        if words and any(word in supplier_words for word in words):
            matches.append(firm['id'])
    if len(matches)==1:
        result['firm_id']=matches[0]
    else:
        result['warnings'].append('Firma eşleştirilemedi veya birden fazla eşleşme var. Seçili firmayı kontrol edin.')
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
