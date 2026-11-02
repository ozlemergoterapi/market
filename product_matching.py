"""Product identity and reviewed aliases. No stock movements are tracked."""
import hashlib
import json
import re
from difflib import SequenceMatcher
from decimal import Decimal
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
    return json.dumps([item['firm_id'],'code:'+code if code else 'name:'+name_key(item['product']),unit_key(item['unit']),package_key(item['product'])],ensure_ascii=False)


def pair_key(left,right):
    return hashlib.sha256(json.dumps(sorted([left,right]),ensure_ascii=False).encode()).hexdigest()


def compare_items(items, decisions=()):
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
    history=[]
    comparisons=[]
    suggestions={}
    unmatched=[]
    for item in items:
        key=identity(item)
        possible=[old for old in reversed(history) if old['firm_id']==item['firm_id'] and old['invoice_id']!=item['invoice_id'] and unit_key(old['unit'])==unit_key(item['unit'])]
        old=None;matched_by=''
        for candidate in possible:
            oldkey=identity(candidate)
            oldpack,newpack=package_key(candidate['product']),package_key(item['product'])
            compatible=not(oldpack[0] and newpack[0] and oldpack[0]!=newpack[0]) and not(oldpack[1] and newpack[1] and oldpack[1]!=newpack[1])
            code=re.sub(r'\s+','',item.get('code','')).upper()
            previous_code=re.sub(r'\s+','',candidate.get('code','')).upper()
            if root(key)==root(oldkey) and key!=oldkey:
                old=candidate;matched_by='Onaylı eşleştirme';break
            if code and code==previous_code and compatible:
                old=candidate;matched_by='Ürün kodu';break
            if compatible and name_key(item['product'])==name_key(candidate['product']) and (not code or not previous_code or code==previous_code):
                old=candidate;matched_by='Ürün adı ve ambalaj';break
        if old:
            comparisons.append(dict(current=item,previous=old,matched_by=matched_by,identity=root(key)))
        elif any(old['firm_id']==item['firm_id'] and old['invoice_id']!=item['invoice_id'] for old in history):
            candidates=[];seen=set()
            for candidate in possible:
                oldkey=identity(candidate);pair=pair_key(key,oldkey)
                if oldkey in seen or pair in rejected: continue
                seen.add(oldkey)
                similarity=SequenceMatcher(None,name_key(item['product']),name_key(candidate['product'])).ratio()
                same_code=bool(item.get('code') and item.get('code')==candidate.get('code'))
                if similarity>=.68 or same_code:
                    candidates.append((similarity,candidate,oldkey,pair))
            for _,candidate,oldkey,pair in sorted(candidates,key=lambda c:c[0],reverse=True)[:3]:
                suggestions[pair]=dict(fingerprint=pair,left_key=key,right_key=oldkey,current=item,previous=candidate)
            if not candidates:
                unmatched.append(item)
        history.append(item)
    return comparisons,list(suggestions.values()),unmatched
