"""Product identity and reviewed aliases. No stock movements are tracked."""
from collections import defaultdict
import hashlib
import json
import re
from difflib import SequenceMatcher
from decimal import Decimal, ROUND_HALF_UP
from invoice_parser import normalized


def unit_key(value):
    value = normalized(value).strip().rstrip('.')
    return {'ad':'adet','pcs':'adet','kilogram':'kg','kilogramme':'kg','lt':'litre','l':'litre'}.get(value,value)


def name_key(value):
    value = normalized(value).replace('×','x')
    value = re.sub(r'(?<=\d)\s+(?=[a-z])|(?<=[a-z])\s+(?=\d)', '',value)
    return re.sub(r'[^a-z0-9]', '',value)


def package_key(value):
    value = normalized(value).replace('×','x')
    sizes=[]
    for match in re.finditer(r'(\d+(?:[.,]\d+)?)\s*(kg|gr|ml|cl|lt|g|l)(?=\W|x|\d|$)',value):
        number=Decimal(match[1].replace(',','.'))
        unit=match[2]
        if unit=='kg': number*=1000;unit='g'
        if unit=='gr': unit='g'
        if unit in ('l','lt'): number*=1000;unit='ml'
        if unit=='cl': number*=10;unit='ml'
        sizes.append(str(number.normalize())+unit)
    packs=re.findall(r'x\s*(\d+)(?!\d)',value)
    return tuple(sizes),tuple(packs)


def identity(item):
    code=re.sub(r'\s+','',item.get('code','')).upper()
    return product_key(item['firm_id'], code, name_key(item['product']), unit_key(item['unit']), package_key(item['product']))


def product_key(firm, code, name, unit, package):
    return json.dumps([firm, 'code:'+code if code else 'name:'+name, unit, package], ensure_ascii=False)


def pair_key(left,right):
    return hashlib.sha256(json.dumps(sorted([left,right]),ensure_ascii=False).encode()).hexdigest()


PACKAGE_UNIT = r'koli|kl|paket|kutu'
PACK_SUFFIX = re.compile(
    rf"(?:\s*[x×]\s*(?P<times>[1-9]\d{{0,3}})"
    rf"|\s+(?P<suffix>[1-9]\d{{0,3}})\s*['’]?\s*l[iıuü])"
    rf"(?:\s*(?:{PACKAGE_UNIT}))?$",
    re.IGNORECASE,
)
UNIT_COUNT = re.compile(rf"(?P<unit>{PACKAGE_UNIT})\s*\(?\s*(?P<count>[1-9]\d{{0,3}})\s+adet\s*\)?")
PRODUCT_SIZE = re.compile(r'(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(kg|gr|ml|cl|lt|g|l)\b')
PACK_BEFORE_SIZE = re.compile(
    rf'\s+(?P<count>[1-9]\d{{0,3}})\s*[x×]\s*(?P<size>{PRODUCT_SIZE.pattern})'
    rf'(?:\s+(?:{PACKAGE_UNIT}))?$',
    re.IGNORECASE,
)


def unpack_product_name(name):
    name = name.strip()
    suffix = PACK_SUFFIX.search(name)
    if suffix:
        return name[:suffix.start()].strip(), int(suffix['times'] or suffix['suffix'])
    prefix = PACK_BEFORE_SIZE.search(name)
    if prefix:
        return name[:prefix.start()] + ' ' + prefix['size'], int(prefix['count'])
    return name, None


def supplier_price_basis(item):
    name, count = unpack_product_name(normalized(item['product']))
    unit = unit_key(item['unit'])
    explicit_unit = UNIT_COUNT.fullmatch(unit)
    unit_count = int(explicit_unit['count']) if explicit_unit else None
    if explicit_unit:
        unit = explicit_unit['unit']
    if unit in ('koli', 'kl', 'paket', 'kutu'):
        if not (count or unit_count) or (count and unit_count and count != unit_count):
            return None
        count = count or unit_count
        unit = 'adet'
    elif unit in ('adet', 'kg', 'litre') and not count:
        count = 1
    else:
        return None

    def normalize_size(match):
        size = package_key(match[0])[0][0]
        unit = 'ml' if size.endswith('ml') else 'g'
        return ' ' + format(Decimal(size[:-len(unit)]), 'f') + unit

    name = ' '.join(PRODUCT_SIZE.sub(normalize_size, name).split())
    if not any(char.isalpha() for char in name):
        return None
    return (name, unit), count


def compare_suppliers(items):
    """Compare each supplier's latest dated purchase on an explicit common unit."""
    groups = defaultdict(dict)
    for item in items:
        basis = supplier_price_basis(item)
        if basis is None:
            continue
        key, count = basis
        date_key = (item['issued'], item['invoice_id'], item['id'])
        previous = groups[key].get(item['firm_id'])
        if previous and previous[0] >= date_key:
            continue
        price = Decimal(item['unit_price']) / count
        groups[key][item['firm_id']] = (date_key, price, count, item)

    results = []
    for (name, unit), suppliers in groups.items():
        if len(suppliers) < 2:
            continue
        ranked = sorted(suppliers.values(), key=lambda entry: (entry[1], entry[3]['firm']))
        latest = max(ranked, key=lambda entry: entry[0])
        rows = [
            dict(item, count=count, common_price=str(price.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)),
                 lowest=price == ranked[0][1])
            for _, price, count, item in ranked
        ]
        label, _ = unpack_product_name(latest[3]['product'])
        results.append(dict(name=name, unit=unit, label=label, suppliers=rows))
    return sorted(results, key=lambda group: (group['name'], group['unit']))


def package_measure(item):
    unit = unit_key(item['unit'])
    if unit in ('kg', 'litre'):
        return unit, Decimal(1)
    basis = supplier_price_basis(item)
    if basis is None:
        raise ValueError('Koli içindeki adet veya ürünün alış birimi belirlenemedi.')
    name, _ = unpack_product_name(normalized(item['product']))
    sizes = list(PRODUCT_SIZE.finditer(name))
    if len(sizes) != 1:
        raise ValueError('Ürün adında karşılaştırılabilir tek bir gramaj veya hacim yazmalı.')
    size = package_key(sizes[0][0])[0][0]
    suffix = 'ml' if size.endswith('ml') else 'g'
    quantity = Decimal(size[:-len(suffix)]) * basis[1] / 1000
    if quantity <= 0:
        raise ValueError('Ambalaj miktarı sıfırdan büyük olmalı.')
    return ('litre' if suffix == 'ml' else 'kg'), quantity


def compare_package_prices(items):
    items = sorted(items, key=lambda item: (item['issued'], item['invoice_id'], item['id']))
    measured = [package_measure(item) for item in items]
    if measured[0][0] != measured[1][0]:
        raise ValueError('Aynı ölçüde iki ürün seçin. Ağırlık ve hacim birlikte karşılaştırılamaz.')
    prices = [Decimal(item['unit_price']) / quantity for item, (_, quantity) in zip(items, measured)]
    difference = prices[1] - prices[0]
    rows = [dict(item, measure=str(quantity), normalized_price=str(price.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)))
            for item, (_, quantity), price in zip(items, measured, prices)]
    return dict(unit=measured[0][0], rows=rows,
                difference=str(difference.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)),
                percent=str((difference * 100 / prices[0]).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)))


def compare_items(items, decisions=(), include_suggestions=True):
    parent={}
    rejected=set()
    def root(key):
        parent.setdefault(key,key)
        while parent[key]!=key:
            parent[key]=parent[parent[key]];key=parent[key]
        return key
    for decision in decisions:
        a,b=decision['left_key'],decision['right_key']
        if decision['decision']=='same':
            ar,br=root(a),root(b)
            parent[max(ar,br)]=min(ar,br)
        else:
            rejected.add(pair_key(a,b))
    aliases = set(parent)
    history = defaultdict(list)
    by_code = defaultdict(list)
    by_name = defaultdict(list)
    by_alias = defaultdict(list)
    latest_invoices = {}
    comparisons = []
    suggestions = {}
    unmatched = []
    for position, item in enumerate(items):
        firm = item['firm_id']
        invoice = item['invoice_id']
        unit = unit_key(item['unit'])
        code = re.sub(r'\s+', '', item.get('code', '')).upper()
        name = name_key(item['product'])
        package = package_key(item['product'])
        key = product_key(firm, code, name, unit, package)
        alias = (firm, unit, root(key))
        group = (firm, unit)
        code_group = (*group, code)
        name_group = (*group, name)

        # Select the latest eligible candidate across the three indexes.
        # A newer match takes precedence regardless of its matching method.
        best = None
        for matches, reason in (
            (by_alias.get(alias, ()), 'Onaylı eşleştirme'),
            (by_code.get(code_group, ()) if code else (), 'Ürün kodu'),
            (by_name.get(name_group, ()), 'Ürün adı ve ambalaj'),
        ):
            for index, previous, old_key, old_code, _, old_package in reversed(matches):
                if best and index <= best[0]:
                    break
                if previous['invoice_id'] == invoice:
                    continue
                if reason == 'Onaylı eşleştirme':
                    if old_key == key:
                        continue
                elif any(a and b and a != b for a, b in zip(package, old_package)):
                    continue
                elif reason == 'Ürün adı ve ambalaj' and code and old_code and code != old_code:
                    continue
                best = (index, previous, reason)
                break

        if best:
            comparisons.append(dict(current=item, previous=best[1], matched_by=best[2], identity=alias[2]))
        elif include_suggestions and (previous_invoices := latest_invoices.get(firm)) and any(old != invoice for old in previous_invoices):
            candidates = []
            seen = set()
            matcher = SequenceMatcher(None, name, '')
            for _, previous, old_key, _, old_name, _ in reversed(history[group]):
                if previous['invoice_id'] == invoice or old_key in seen:
                    continue
                seen.add(old_key)
                same_code = bool(item.get('code') and item['code'] == previous.get('code'))
                matcher.set_seq2(old_name)
                if not same_code and matcher.quick_ratio() < .68:
                    continue
                similarity = matcher.ratio()
                if similarity < .68 and not same_code:
                    continue
                pair = pair_key(key, old_key)
                if pair not in rejected:
                    candidates.append((similarity, previous, old_key, pair))
            for _, previous, old_key, pair in sorted(candidates, key=lambda candidate: candidate[0], reverse=True)[:3]:
                suggestions[pair] = dict(fingerprint=pair, left_key=key, right_key=old_key, current=item, previous=previous)
            if not candidates:
                unmatched.append(item)

        entry = (position, item, key, code, name, package)
        history[group].append(entry)
        if code:
            by_code[code_group].append(entry)
        by_name[name_group].append(entry)
        if key in aliases:
            by_alias[alias].append(entry)
        recent = latest_invoices.setdefault(firm, set())
        if len(recent) < 2:
            recent.add(invoice)
    return comparisons, list(suggestions.values()), unmatched
