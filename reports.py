"""Period summaries over the local cari ledger."""
from collections import defaultdict
from datetime import date


MOVEMENT_FIELDS = ('purchases', 'returns', 'payments', 'openings')
LEDGER_MOVEMENTS = """
    SELECT firm_id, issued AS day, 'purchases' AS kind, total AS amount FROM invoices
    UNION ALL SELECT firm_id, entry_date,
        CASE kind WHEN 'opening' THEN 'openings' ELSE 'returns' END, amount FROM account_entries
    UNION ALL SELECT firm_id, paid_on, 'payments', amount FROM firm_payments
    UNION ALL SELECT i.firm_id, p.paid_on, 'payments', p.amount
        FROM payments p JOIN invoices i ON i.id=p.invoice_id
    UNION ALL SELECT a.firm_id, p.paid_on, 'payments', p.amount
        FROM opening_payments p JOIN account_entries a ON a.id=p.opening_id
"""


def movement_totals():
    return {key: 0 for key in (*MOVEMENT_FIELDS, 'payment_count')}


def add_movement(totals, row):
    totals[row['kind']] += row['amount']
    if row['kind'] == 'payments':
        totals['payment_count'] += row['count']


def balance_change(totals):
    return totals['purchases'] + totals['openings'] - totals['returns'] - totals['payments']


def balance_split(balances):
    debt = sum(max(0, amount) for amount in balances)
    credit = sum(max(0, -amount) for amount in balances)
    return debt, credit


def remaining_after_firm_payment(firm_id, amount, available):
    applied = min(max(0, amount), available.get(firm_id, 0))
    available[firm_id] = available.get(firm_id, 0) - applied
    return amount - applied


def due_breakdown(db, end, suppliers):
    obligations = db.execute('''
        SELECT i.id, i.firm_id, i.due, i.issued AS booked, 'invoice' AS kind,
            i.total - COALESCE((SELECT SUM(p.amount) FROM payments p
                WHERE p.invoice_id=i.id AND p.paid_on<=:end),0)
            - COALESCE((SELECT SUM(a.amount) FROM account_entries a
                WHERE a.invoice_id=i.id AND a.kind='credit' AND a.entry_date<=:end),0) AS remaining
        FROM invoices i WHERE i.issued<=:end
        UNION ALL
        SELECT a.id, a.firm_id, a.due, a.entry_date, 'opening',
            a.amount - COALESCE((SELECT SUM(p.amount) FROM opening_payments p
                WHERE p.opening_id=a.id AND p.paid_on<=:end),0)
        FROM account_entries a WHERE a.kind='opening' AND a.entry_date<=:end
        ORDER BY due,booked,kind,id
    ''', {'end':end}).fetchall()
    before_allocation = defaultdict(int)
    for entry in obligations:
        before_allocation[entry['firm_id']] += max(0, entry['remaining'])
    available = {firm_id: max(0, before_allocation[firm_id] - supplier['closing'])
                 for firm_id, supplier in suppliers.items()}
    for entry in obligations:
        if entry['remaining'] <= 0 or entry['firm_id'] not in suppliers:
            continue
        balance = remaining_after_firm_payment(entry['firm_id'], entry['remaining'], available)
        bucket = 'overdue' if entry['due'] < end else 'due_today' if entry['due'] == end else 'future'
        suppliers[entry['firm_id']][bucket] += balance


def range_report(db, start, end, previous_start=None, previous_end=None):
    for value in (start, end, previous_start, previous_end):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('Geçerli bir tarih aralığı seçin.')
    if start > end or bool(previous_start) != bool(previous_end) or (previous_end and not previous_start <= previous_end < start):
        raise ValueError('Geçerli bir tarih aralığı seçin.')
    suppliers = {
        row['id']: dict(row, opening=0, closing=0, current=movement_totals(),
                        previous=movement_totals(), overdue=0, due_today=0, future=0)
        for row in db.execute('SELECT id,name,archived FROM firms WHERE merged_into IS NULL ORDER BY name')
    }
    current = movement_totals()
    previous = movement_totals()
    monthly = defaultdict(movement_totals)
    monthly_changes = defaultdict(lambda: defaultdict(int))
    rows = db.execute(f'''
        SELECT firm_id,day,kind,SUM(amount) AS amount,COUNT(*) AS count
        FROM ({LEDGER_MOVEMENTS}) WHERE day<=?
        GROUP BY firm_id,day,kind ORDER BY day
    ''', (end,))
    for row in rows:
        firm_id = row['firm_id']
        if firm_id not in suppliers:
            continue
        supplier = suppliers[firm_id]
        day, kind, amount = row['day'], row['kind'], row['amount']
        delta = amount if kind in ('purchases', 'openings') else -amount
        if day < start:
            supplier['opening'] += delta
            if previous_start and previous_start <= day <= previous_end:
                add_movement(supplier['previous'], row)
                add_movement(previous, row)
        else:
            add_movement(supplier['current'], row)
            add_movement(current, row)
            month = day[:7]
            add_movement(monthly[month], row)
            monthly_changes[month][firm_id] += delta

    balances = {firm_id: supplier['opening'] for firm_id, supplier in suppliers.items()}
    opening_debt, opening_credit = balance_split(balances.values())
    for supplier in suppliers.values():
        supplier['closing'] = supplier['opening'] + balance_change(supplier['current'])
    closing_debt, closing_credit = balance_split(supplier['closing'] for supplier in suppliers.values())
    due_breakdown(db, end, suppliers)

    months = []
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    month_count = (last.year-first.year)*12 + last.month-first.month+1
    # A normal annual report includes quiet months too. A long custom range
    # lists activity months to keep the table usable.
    if month_count <= 24:
        for offset in range(month_count):
            year, month = divmod(first.year*12 + first.month-1+offset, 12)
            monthly[f'{year:04d}-{month+1:02d}']
    for month, amounts in sorted(monthly.items()):
        for firm_id, delta in monthly_changes[month].items():
            balances[firm_id] += delta
        debt, credit = balance_split(balances.values())
        months.append(dict(month=month, **amounts, closing_debt=debt, closing_credit=credit))

    return dict(current=current, previous=previous, opening_debt=opening_debt, opening_credit=opening_credit,
                closing_debt=closing_debt, closing_credit=closing_credit, suppliers=list(suppliers.values()),
                months=months)
