import tempfile
import unittest
from datetime import date, datetime
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
        data = dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',duplicate_confirmed=True,
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

    def test_opening_balance_is_due_and_decreases_with_payment(self):
        app.create_account_entry(dict(kind='opening',firm_id=1,entry_date='2026-01-01',due='2026-02-09',amount='500.00',reference='2025 devri'))
        value=app.state(date(2026,2,9))
        self.assertEqual(value['totals']['today'],50000)
        self.assertEqual(value['firms'][0]['balance'],50000)
        self.assertEqual(value['obligations'][0]['kind'],'opening')
        app.create_payment(dict(target_type='opening',target_id=1,amount='125.00',paid_on='2026-02-01'))
        value=app.state(date(2026,2,9))
        self.assertEqual(value['totals']['today'],37500)
        self.assertEqual(value['payments'][0]['target_type'],'opening')
        with self.assertRaisesRegex(ValueError,'kalan borcunu aşamaz'):
            app.create_payment(dict(target_type='opening',target_id=1,amount='375.01',paid_on='2026-02-01'))

    def test_credit_note_reduces_linked_invoice_and_due_total(self):
        self.invoice()
        app.create_account_entry(dict(kind='credit',firm_id=1,invoice_id=1,entry_date='2026-02-01',due='2026-02-01',amount='100.50',reference='IADE-1'))
        value=app.state(date(2026,2,9))
        self.assertEqual(value['invoices'][0]['credited'],10050)
        self.assertEqual(value['invoices'][0]['remaining'],90000)
        self.assertEqual(value['totals']['today'],90000)
        with self.assertRaisesRegex(ValueError,'kalan borcu aşamaz'):
            app.create_payment(dict(invoice_id=1,amount='900.01',paid_on='2026-02-01'))
        with self.assertRaisesRegex(ValueError,'kalan borcunu aşamaz'):
            app.create_account_entry(dict(kind='credit',firm_id=1,invoice_id=1,entry_date='2026-02-01',due='2026-02-01',amount='900.01'))
        app.delete_record('entry',1)
        self.assertEqual(app.state(date(2026,2,9))['totals']['today'],100050)

    def test_selected_backup_directory_is_persisted_and_resettable(self):
        selected=Path(self.tmp.name)/'harici-yedek'
        app.set_backup_directory(selected)
        self.assertEqual(app.backup_directory(),selected.resolve())
        self.assertEqual(app.settings()['backup_directory'],str(selected.resolve()))
        self.invoice()
        self.assertEqual(app.automatic_backup(datetime(2026,1,2,8,0,0)).parent,selected.resolve())
        app.set_backup_directory()
        self.assertEqual(app.backup_directory(),app.DB.parent/'backups')

    def test_price_comparison_uses_chronology_and_unit(self):
        self.invoice(number='NEW',issued='2026-02-01',items=[dict(product='Marka süt',unit='1 litre / adet',price='39')])
        self.invoice(number='OLD')
        alerts = app.state()['alerts']
        self.assertEqual(len(alerts),1)
        self.assertEqual(alerts[0]['old_price'],3500)
        self.assertEqual(alerts[0]['price'],3900)
        self.assertEqual(alerts[0]['change'],11.43)
        history = app.state()['price_histories'][0]
        self.assertEqual(history['id'],alerts[0]['history_id'])
        self.assertEqual([point['price'] for point in history['points']],[3500,3900])
        self.invoice(number='OTHER',issued='2026-03-01',items=[dict(product='Marka süt',unit='500 ml / adet',price='20')])
        self.assertEqual(len(app.state()['alerts']),1)

    def test_startup_backup_is_consistent_and_retains_newest_files(self):
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Yedek Firma')")
        first = app.automatic_backup(datetime(2026,1,2,8,0,0),keep=2)
        with app.sqlite3.connect(first) as backup:
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM firms').fetchone()[0],2)
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Sonradan Eklendi')")
        app.automatic_backup(datetime(2026,1,2,9,0,0),keep=2)
        latest = app.automatic_backup(datetime(2026,1,2,10,0,0),keep=2)
        self.assertFalse(first.exists())
        self.assertTrue(latest.exists())
        self.assertEqual(app.backup_status()['count'],2)

    def test_backup_can_be_restored_with_a_safety_snapshot(self):
        self.invoice()
        saved = app.automatic_backup(datetime(2026,1,3,8,0,0))
        self.invoice(number='F002')
        app.restore_backup(saved.name)
        self.assertEqual([invoice['number'] for invoice in app.state()['invoices']],['F001'])
        self.assertGreaterEqual(app.backup_status()['count'],2)
        with self.assertRaisesRegex(ValueError,'Geçerli bir yedek'):
            app.restore_backup('../market.sqlite3')

    def test_duplicate_invoice_requires_explicit_review_and_rejects_same_file(self):
        self.invoice()
        duplicate=dict(firm_id=1,number='F002',issued='2026-01-20',days=20,total='1000.50',items=[])
        with self.assertRaisesRegex(ValueError,'MÜKERRER_ONAYI'):
            app.create_invoice(duplicate)
        duplicate['duplicate_confirmed']=True
        app.create_invoice(duplicate)
        picture='data:image/png;base64,YWJj'
        app.create_invoice(dict(firm_id=1,number='PHOTO1',issued='2026-02-01',days=1,total='10',photos=[picture],items=[]))
        with self.assertRaisesRegex(ValueError,'Aynı fatura dosyası'):
            app.create_invoice(dict(firm_id=1,number='PHOTO2',issued='2026-02-02',days=1,total='11',photos=[picture],items=[]))

    def test_confirmed_ocr_supplier_alias_is_learned(self):
        self.invoice(ocr_supplier='GUNES GIDA PAZARLAMA LTD')
        firm=app.state()['firms'][0]
        self.assertEqual(firm['ocr_samples'],1)
        self.assertIn('GUNES GIDA',firm['ocr_aliases'])

    def test_invalid_amounts_and_negative_term(self):
        for value in ['NaN','Infinity','0','-1','1.001']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.money(value)
        with self.assertRaises(ValueError):
            self.invoice(days=-1)
        self.assertEqual(len(app.state()['invoices']),0)

    def test_empty_firm_can_be_deleted(self):
        app.delete_firm(1)
        self.assertEqual(app.state()['firms'], [])
        with self.assertRaises(ValueError):
            app.delete_firm(1)

    def test_firm_with_paid_invoice_cannot_be_deleted(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='1000.50',paid_on='2026-01-21'))
        with self.assertRaises(ValueError):
            app.delete_firm(1)
        self.assertEqual(len(app.state()['firms']),1)
        self.assertEqual(len(app.state()['payments']),1)

    def test_invoice_delete_removes_children_and_recalculates(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='200',paid_on='2026-01-21'))
        with self.assertRaises(ValueError):
            app.delete_record('invoice',1,dict(invoices=1,payments=0,items=1))
        self.assertEqual(len(app.state()['invoices']),1)
        app.delete_record('invoice',1,dict(invoices=1,payments=1,items=1))
        value=app.state()
        self.assertEqual(value['totals']['total'],0)
        self.assertEqual(value['payments'],[])
        self.assertEqual(value['items'],[])

    def test_payment_delete_restores_debt(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='200',paid_on='2026-01-21'))
        app.delete_record('payment',1)
        self.assertEqual(app.state()['totals']['total'],100050)

    def test_firm_delete_cascades_after_explicit_counts(self):
        self.invoice()
        app.create_payment(dict(invoice_id=1,amount='200',paid_on='2026-01-21'))
        app.delete_record('firm',1,dict(invoices=1,payments=1,items=1))
        value=app.state()
        self.assertEqual(value['firms'],[])
        self.assertEqual(value['invoices'],[])
        self.assertEqual(value['payments'],[])

    def test_four_decimal_unit_price_is_retained(self):
        self.invoice(items=[dict(product='Süt',unit='adet',price='26.3789')])
        value=app.state()['items'][0]
        self.assertEqual(value['unit_price'],'26.3789')
        self.assertAlmostEqual(value['price'],2637.89)
        self.invoice(number='F002',issued='2026-02-01',items=[dict(product='Süt',unit='adet',price='26.3790')])
        self.assertEqual(len(app.state()['alerts']),1)

    def test_quantity_and_product_code_are_saved_and_editable(self):
        self.invoice(items=[dict(product='Süt 200MLX24',unit='adet',price='26.3789',quantity='18',code='8223298',net='470.13',vat='4.69')])
        item=app.state()['items'][0]
        self.assertEqual(item['quantity'],'18')
        self.assertEqual(item['code'],'8223298')
        self.assertEqual(item['source_net'],'470.13')
        app.create_invoice(dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',items=[dict(product='Süt 200MLX24',unit='adet',price='39.5683',quantity='12',code='8223298')]),1)
        self.assertEqual(app.state()['items'][0]['quantity'],'12')

    def test_invalid_quantity_does_not_save_invoice(self):
        for quantity in ['0','-2','NaN','', '1.00001']:
            with self.subTest(quantity=quantity),self.assertRaises(ValueError):
                self.invoice(items=[dict(product='Süt',unit='adet',price='35',quantity=quantity)])
        self.assertEqual(app.state()['invoices'],[])

    def test_imported_invoice_cannot_save_with_missing_rows(self):
        data=dict(firm_id=1,number='CHECKED',issued='2026-01-20',days=20,total='719.09',import_check=dict(expected_rows=[1,2]),
                  items=[dict(row_number=1,product='Süt 1L',unit='adet',code='12345',quantity='18',price='26.38',line_total='474.82')])
        with self.assertRaises(ValueError):
            app.create_invoice(data)
        self.assertEqual(app.state()['invoices'],[])
        data['items'].append(dict(row_number=2,product='Su 1L',unit='adet',code='67890',quantity='18',price='13.57',line_total='244.27'))
        app.create_invoice(data)
        value=app.state()
        self.assertEqual(len(value['items']),2)
        self.assertEqual(value['items'][0]['row_number'],'1')
        data['items'].pop()
        data.pop('import_check')
        with self.assertRaises(ValueError):
            app.create_invoice(data,1)
        self.assertEqual(len(app.state()['items']),2)

    def test_code_matches_changed_product_name_and_returns_price_alert(self):
        self.invoice(items=[dict(product='BENIMO KOMBO 96GX18',unit='adet',price='35',code='8223298')])
        self.invoice(number='F002',issued='2026-02-01',items=[dict(product='BENIMO K0MBO 96G X18',unit='adet',price='39',code='8223298')])
        alerts=app.state()['alerts']
        self.assertEqual(len(alerts),1)
        self.assertEqual(alerts[0]['matched_by'],'Ürün kodu')

    def test_confirmed_alias_generates_alert_and_can_be_undone(self):
        self.invoice(items=[dict(product='BENIMO KOMBO 96GX18',unit='adet',price='35')])
        self.invoice(number='F002',issued='2026-02-01',items=[dict(product='BENIMO K0MBO 96G X18',unit='adet',price='39')])
        value=app.state()
        self.assertEqual(value['alerts'],[])
        suggestion=value['product_suggestions'][0]
        with app.connect() as db:
            db.execute('INSERT INTO product_matches VALUES(?,?,?,?,?)',(suggestion['fingerprint'],suggestion['left_key'],suggestion['right_key'],'same','2026-02-01'))
        self.assertEqual(len(app.state()['alerts']),1)
        with app.connect() as db:
            db.execute('DELETE FROM product_matches')
        self.assertEqual(app.state()['alerts'],[])
        self.assertEqual(len(app.state()['product_suggestions']),1)

    def test_duplicate_invoice_rolls_back(self):
        self.invoice()
        with self.assertRaisesRegex(ValueError,'aynı fatura numarası'):
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
