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


def _item_identity(item):
    """Fields that must agree before two OCR rows can be treated as one row."""
    try:
        quantity = Decimal(str(item.get('quantity', ''))).normalize()
        line_total = Decimal(str(item.get('line_total', ''))).normalize()
        net = Decimal(str(item.get('net', ''))).normalize()
        vat = Decimal(str(item.get('vat', ''))).normalize()
    except InvalidOperation:
        return None
    code = re.sub(r'[^A-Z0-9]', '', str(item.get('code', '')).upper())
    return code, quantity, line_total, net, vat


def _item_score(item):
    fields = ('code', 'product', 'unit', 'price', 'quantity', 'source_price', 'net', 'vat', 'line_total')
    return sum(bool(str(item.get(key, '')).strip()) for key in fields), len(str(item.get('product', '')).strip()), not bool(item.get('review'))


def _consolidate_items(items, invalid_rows=(), allow_consensus=False):
    """Merge repeated page/OCR rows only when their invoice evidence agrees."""
    grouped = {}
    for item in items:
        grouped.setdefault(int(item['row_number']), []).append(item)
    invalid_rows = set(invalid_rows)
    consolidated, warnings = [], []
    for row_number in sorted(set(grouped) | invalid_rows):
        candidates = grouped.get(row_number, [])
        identities = {_item_identity(item) for item in candidates}
        if row_number in invalid_rows or not candidates or None in identities:
            warnings.append(f'{row_number}. ürün satırı OCR okumalarında çelişkili veya eksik çıktı; otomatik doldurulmadı.')
            continue
        selected = candidates
        if len(identities) != 1:
            counts = {identity: sum(_item_identity(item) == identity for item in candidates) for identity in identities}
            ordered = sorted(counts.items(), key=lambda entry: entry[1], reverse=True)
            if not allow_consensus or ordered[0][1] < 2 or ordered[0][1] == ordered[1][1]:
                warnings.append(f'{row_number}. ürün satırı OCR okumalarında çelişkili veya eksik çıktı; otomatik doldurulmadı.')
                continue
            selected = [item for item in candidates if _item_identity(item) == ordered[0][0]]
            warnings.append(f'{row_number}. ürün satırı farklı OCR düzenlerinde değişik okundu; birden fazla aynı çıkan okuma kullanıldı ve faturayla kontrol edilmeli.')
        consolidated.append(max(selected, key=_item_score))
    return consolidated, warnings


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
    matches=set()
    for firm in firms:
        words=[w for w in re.findall(r'[a-z0-9]+',normalized(firm['name'])) if len(w)>3 and w not in generic]
        if words and any(word in supplier_words for word in words):
            matches.add(firm['id'])
    if len(matches)==1:
        result['firm_id']=next(iter(matches))
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
    parsed_items, invalid_rows = [], set()
    for block in blocks:
        row_number = int(block.split()[0])
        if not 1 <= row_number <= 500:
            continue
        match = re.match(r'^\d{1,3}\s+(\d{5,14})\s+(.+?)\s+(\d+(?:[.,]\d+)*)\s*(Adet|Ad\.?|Kg|Koli|Lt|Litre)\s+(.+)', block, re.I)
        if not match:
            invalid_rows.add(row_number)
            result['warnings'].append('Bir ürün satırı okunamadı; fotoğrafla karşılaştırın.')
            continue
        code, product, quantity, unit, tail = match.groups()
        amounts = re.findall(AMOUNT, tail)
        try:
            qty = decimal_value(quantity) if ',' in quantity else Decimal(quantity.replace('.','') if re.fullmatch(r'\d{1,3}(?:\.\d{3})+',quantity) else quantity)
            if qty <= 0 or qty > 99999999 or qty != qty.quantize(Decimal('.0001')) or len(amounts) < 4:
                raise ValueError()
            # Supported table ends: unit price, VAT %, VAT amount, line net amount.
            vat, net = map(decimal_value, amounts[-2:])
            raw_price = decimal_value(amounts[0])
            if vat < 0 or net <= 0 or raw_price <= 0:
                raise ValueError()
            price = ((net + vat) / qty).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            review = ''
            if net > qty*raw_price+Decimal('.10') or net < qty*raw_price*Decimal('.5'):
                review = 'Miktar ile satır/birim fiyatı arasında büyük fark var. Adet, koli ve iskonto bilgilerini kontrol edin.'
            parsed_items.append(dict(row_number=row_number, line_total=str(net+vat), review=review, product=product[:200], unit=unit.lower(), price=str(price), code=code,
                                     quantity=str(qty), source_price=str(raw_price), net=str(net), vat=str(vat)))
        except (InvalidOperation, ValueError):
            invalid_rows.add(row_number)
            result['warnings'].append('Bir ürünün son birim fiyatı hesaplanamadı; elle kontrol edin.')
    result['items'], duplicate_warnings = _consolidate_items(parsed_items, invalid_rows)
    result['warnings'].extend(duplicate_warnings)
    result['warnings'].extend('Ürün kodu '+item['code']+': '+item['review'] for item in result['items'] if item.get('review'))
    # A row starts with a sequence number followed by a numeric product code.
    # Product descriptions can also begin with values such as "120 GX20KL";
    # requiring at least five digits in the code prevents those continuations
    # from inflating the expected row count.
    detected = set()
    for line in lines:
        match = re.match(r'^(\d{1,3})\s+([A-Z0-9]{5,14})\b', line, re.I)
        if match and sum(character.isdigit() for character in match[2]) >= 5:
            row_number = int(match[1])
            if 1 <= row_number <= 500:
                detected.add(row_number)
    expected = list(range(1,max(detected)+1)) if detected else []
    result['import_check'] = {'expected_rows':expected,'detected_rows':sorted(detected)}
    result['coverage'] = check_items(result['items'],result['total'],result['import_check'])
    result['warnings'].extend(result['coverage']['errors'])
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


def check_items(items, total, metadata):
    """Checks coverage and exact source line totals, not OCR semantic correctness."""
    errors=[]
    expected=metadata.get('expected_rows',[]) if isinstance(metadata,dict) else []
    if not isinstance(expected,list) or any(not isinstance(n,int) or not 1<=n<=500 for n in expected):
        return dict(errors=['Fatura kalem kontrol bilgisi geçersiz. Yeniden okutun.'],read=len(items),expected=0,line_total='0.00')
    if not expected:
        errors.append('Faturadaki kalem sayısı belirlenemedi. Yeniden okutun.')
    numbers=[]
    gross=Decimal('0')
    for item in items:
        try:
            number=int(item.get('row_number') or 0)
            quantity=Decimal(str(item.get('quantity') or '0'))
            price=Decimal(str(item.get('price') or '0'))
            amount=Decimal(str(item.get('line_total') or 'NaN'))
            if not all(v.is_finite() for v in (quantity,price,amount)) or quantity<=0 or price<=0 or amount<=0 or amount>999999999:
                raise ValueError()
            if abs(quantity*price-amount)>quantity*Decimal('.005')+Decimal('.02'):
                errors.append(f'{number}. satırın miktarı × birim fiyatı, satır toplamıyla uyuşmuyor.')
            gross+=amount
            numbers.append(number)
        except (ValueError,TypeError,InvalidOperation):
            errors.append('Ürün miktarı, fiyatı, sıra no ve KDV dahil satır toplamı eksiksiz girilmeli.')
    missing=sorted(set(expected)-set(numbers))
    if missing:
        errors.append('Eksik fatura kalemleri: '+', '.join(map(str,missing))+'. Eksik kalemlerle kaydedilemez.')
    if len(numbers)!=len(set(numbers)):
        errors.append('Aynı sıra numarası birden fazla kez girilmiş.')
    if set(numbers)-set(expected):
        errors.append('Faturanın sıra numaralarıyla ürün listesi uyuşmuyor.')
    try:
        target=Decimal(str(total))
        if not target.is_finite() or target<=0 or abs(target-gross)>Decimal('.02'):
            errors.append('KDV dahil ürün satır toplamları, ödenecek fatura toplamıyla uyuşmuyor.')
    except InvalidOperation:
        errors.append('Fatura toplamı okunamadı veya eksik.')
    return dict(errors=list(dict.fromkeys(errors)),read=len(items),expected=len(expected),line_total=str(gross.quantize(Decimal('.01'))))


def merge_readings(readings):
    """Merge complementary OCR layouts, rejecting rows whose evidence conflicts."""
    result=dict(number='',issued='',total='',supplier='',firm_id=None,items=[],warnings=[],sources={})
    for key,label in [('number','Fatura numarası'),('issued','Fatura tarihi'),('total','Fatura toplamı')]:
        values={r[key] for r in readings if r.get(key)}
        if len(values)==1:
            result[key]=next(iter(values))
            result['sources'][key]=next(r.get('sources',{}).get(key,r[key]) for r in readings if r.get(key))
        elif len(values)>1:
            result['warnings'].append(label+' farklı okumalarda çelişkili çıktı; fotoğrafla kontrol edin.')
        else:
            result['warnings'].append(label+' okunamadı; elle girin.')
    firms={r['firm_id'] for r in readings if r.get('firm_id')}
    if len(firms)==1:
        result['firm_id']=next(iter(firms))
    else:
        result['warnings'].append('Firma eşleşmesini kontrol edin.')
    result['supplier']=next((r['supplier'] for r in readings if r.get('firm_id')==result['firm_id'] and r.get('supplier')),next((r['supplier'] for r in readings if r.get('supplier')),''))
    detected=sorted({n for r in readings for n in r.get('import_check',{}).get('detected_rows',[]) if isinstance(n,int) and 1<=n<=500})
    expected_fallback=sorted({n for r in readings for n in r.get('import_check',{}).get('expected_rows',[]) if isinstance(n,int) and 1<=n<=500})
    ignored=[]
    if 1 in detected:
        missing_run=0
        maximum=0
        detected_set=set(detected)
        for number in range(1,max(detected)+1):
            if number in detected_set:
                maximum=number
                missing_run=0
            else:
                missing_run+=1
                if missing_run>=4:
                    maximum=number-missing_run
                    break
        expected=list(range(1,maximum+1))
        ignored=[number for number in detected if number>maximum]
    else:
        expected=expected_fallback
    result['import_check']={'expected_rows':expected,'detected_rows':detected}
    candidates=[item for reading in readings for item in reading.get('items',[])]
    candidates=[item for item in candidates if item.get('row_number') in set(expected)]
    result['items'], item_warnings = _consolidate_items(candidates, allow_consensus=True)
    result['warnings'].extend(item_warnings)
    if ignored:
        result['warnings'].append('Ürün tablosunun dışında okunan sıra numarası adayları göz ardı edildi: '+', '.join(map(str,ignored))+'.')
    result['warnings'].extend(
        warning for reading in readings
        for warning in reading.get('table_warnings', [])
        if not warning.startswith('Eksik fatura kalemleri:')
    )
    result['coverage']=check_items(result['items'],result['total'],result['import_check'])
    result['warnings'].extend(result['coverage']['errors'])
    result['warnings'].extend(i['review'] for i in result['items'] if i.get('review'))
    result['warnings'].append('Vade gününü girin; ürünleri ve miktarları fotoğrafla kontrol ederek onaylayın.')
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result
