import tempfile
import unittest
from datetime import date
from pathlib import Path
import app


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = app.DB
        app.DB = Path(self.tmp.name)/'test.sqlite3'
        app.init()
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Güneş Gıda')")

    def tearDown(self):
        app.DB = self.original
        self.tmp.cleanup()

    def invoice(self, **changes):
        data = dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',
                    items=[dict(product='Marka süt',unit='1 litre / adet',price='35.00')])
        data.update(changes)
        app.create_invoice(data)

    def test_due_partial_payment_and_dashboard(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='200.25',paid_on='2026-01-21'))
        state = app.state(date(2026,2,9))
        self.assertEqual(state['invoices'][0]['due'],'2026-02-09')
        self.assertEqual(state['totals'],dict(today=80025,overdue=0,upcoming=0,total=80025))
        self.assertEqual(app.state(date(2026,2,10))['totals']['overdue'],80025)
        self.assertEqual(app.state(date(2026,2,2))['totals']['upcoming'],80025)
        self.assertEqual(app.state(date(2026,2,1))['totals']['upcoming'],0)
        with self.assertRaises(ValueError):
            app.create_payment(dict(invoice_id=1,amount='800.26',paid_on='2026-01-21'))
        app.create_payment(dict(invoice_id=1,amount='800.25',paid_on='2026-01-22'))
        self.assertEqual(app.state(date(2026,2,10))['totals']['total'],0)

    def test_price_comparison_uses_chronology_and_unit(self):
        self.invoice(number='NEW',issued='2026-02-01',items=[dict(product='Marka süt',unit='1 litre / adet',price='39')])
        self.invoice(number='OLD')
        alerts = app.state()['alerts']
        self.assertEqual(len(alerts),1)
        self.assertEqual(alerts[0]['old_price'],3500)
        self.assertEqual(alerts[0]['price'],3900)
        self.assertEqual(alerts[0]['change'],11.43)
        self.invoice(number='OTHER',issued='2026-03-01',items=[dict(product='Marka süt',unit='500 ml / adet',price='20')])
        self.assertEqual(len(app.state()['alerts']),1)

    def test_invalid_amounts_and_negative_term(self):
        for value in ['NaN','Infinity','0','-1','1.001']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.money(value)
        with self.assertRaises(ValueError):
            self.invoice(days=-1)
        self.assertEqual(len(app.state()['invoices']),0)

    def test_duplicate_invoice_rolls_back(self):
        self.invoice()
        with self.assertRaises(app.sqlite3.IntegrityError):
            self.invoice()
        self.assertEqual(len(app.state()['invoices']),1)

    def test_invoice_correction_updates_due_balance_and_preserves_payment(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='200',paid_on='2026-01-21'))
        app.create_invoice(dict(firm_id=1,number='FIXED',issued='2026-01-20',days=30,total='900',items=[]),1)
        value=app.state(date(2026,2,19))
        self.assertEqual(value['totals']['today'],70000)
        self.assertEqual(value['invoices'][0]['number'],'FIXED')
        with self.assertRaises(ValueError):
            app.create_invoice(dict(firm_id=1,number='FIXED',issued='2026-01-20',days=30,total='100',items=[]),1)
        self.assertEqual(app.state()['invoices'][0]['total'],90000)

    def test_payment_correction_and_reassignment(self):
        self.invoice()
        self.invoice(number='F002')
        app.create_payment(dict(invoice_id=1,amount='200',paid_on='2026-01-21'))
        app.create_payment(dict(invoice_id=2,amount='300',paid_on='2026-01-22'),1)
        values=app.state()['invoices']
        self.assertEqual([i['remaining'] for i in values],[100050,70050])
        with self.assertRaises(ValueError):
            app.create_payment(dict(invoice_id=2,amount='1000.51',paid_on='2026-01-22'),1)
        self.assertEqual(app.state()['payments'][0]['amount'],30000)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM edits').fetchone()[0],1)

    def test_price_approval_invalidates_when_either_price_changes(self):
        self.invoice()
        self.invoice(number='F002',issued='2026-02-01',items=[dict(product='Marka süt',unit='1 litre / adet',price='35.01')])
        alert=app.state()['alerts'][0]
        self.assertFalse(alert['approved'])
        with app.connect() as db:
            db.execute('INSERT INTO price_reviews VALUES(?,?)',(alert['fingerprint'],'2026-02-01'))
        self.assertTrue(app.state()['alerts'][0]['approved'])
        app.create_invoice(dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',items=[dict(product='Marka süt',unit='1 litre / adet',price='36')]),1)
        changed=app.state()['alerts'][0]
        self.assertFalse(changed['approved'])
        self.assertLess(changed['change'],0)


if __name__ == '__main__':
    unittest.main()
