import tempfile
import unittest
import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
import app
from desktop_launcher import add_ocr_paths, resource_path, storage_directory


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

    def test_deleted_invoice_draft_cannot_attach_to_reused_id(self):
        document = dict(firm_id=1, number='OLD', issued='2026-01-01', days=10, total='100')
        first = app.create_invoice(document)
        original_identity = app.state()['invoices'][0]['identity']
        key = 'invoice:uid:' + original_identity
        payload = {'fields':[{'name':'number','value':'OLD-EDIT'}]}
        app.save_draft(key, payload)
        app.delete_record('invoice', first, {'invoices':1})
        second = app.create_invoice(dict(document, number='NEW', total='200'))
        self.assertEqual(second, first)
        self.assertIsNone(app.load_draft(key))
        with self.assertRaisesRegex(ValueError, 'artık kayıtlı değil'):
            app.save_draft(key, payload)
        with self.assertRaisesRegex(ValueError, 'artık kayıtlı değil'):
            app.save_draft(f'invoice:{second}', payload)
        with self.assertRaisesRegex(ValueError, 'aynı kayıt değil'):
            app.create_invoice(dict(document, invoice_identity=original_identity), record_id=second)
        with app.connect() as db:
            db.execute("INSERT INTO form_drafts VALUES(?, '{}', '2026-01-01')", (f'invoice:{second}',))
        app.init()
        self.assertIsNone(app.load_draft(f'invoice:{second}'))
        self.assertEqual(app.state()['invoices'][0]['number'], 'NEW')

    def test_restore_rejects_invalid_schema_without_replacing_active_database(self):
        folder = app.backup_directory()
        folder.mkdir()
        source = folder/'market-2026-01-03_00-00-00-000000.sqlite3'
        with app.sqlite3.connect(source) as db:
            for table in ('firms','invoices','items','payments'):
                db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        original = app.DB.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Seçilen yedek açılamadı'):
            app.restore_backup(source.name)
        self.assertEqual(app.DB.read_bytes(), original)
        self.assertEqual(app.state()['firms'][0]['name'], 'Güneş Gıda')

    def test_ettn_is_unique_across_firms_and_cannot_be_overridden(self):
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Yıldız Gıda')")
        document = dict(firm_id=1, number='G-1', issued='2026-01-01', days=30, total='100')
        ettn = '13a2cb00-381d-4bf7-8aa0-123456789abc'
        app.create_invoice(dict(document, ettn=ettn.upper()))
        app.create_invoice(dict(document, days=45), record_id=1)
        self.assertEqual(app.state()['invoices'][0]['ettn'], ettn)
        another = dict(document, firm_id=2, number='Y-1', ettn=ettn.replace('-', ''), duplicate_confirmed=True)
        with self.assertRaisesRegex(ValueError, 'G-1 numaralı faturada'):
            app.create_invoice(another)
        undo = app.delete_record('invoice', 1, {'invoices':1})
        app.create_invoice(another)
        with self.assertRaisesRegex(ValueError, 'ETTN başka bir faturada'):
            app.restore_deleted_record(undo)

    def test_firm_merge_moves_records_and_takes_premerge_backup(self):
        with app.connect() as db:
            db.execute("INSERT INTO firms(name,default_due_days) VALUES('Güneş Gıda Ltd.',30)")
            db.execute("UPDATE firms SET phone='+90 555 123 45 67' WHERE id=1")
        app.create_invoice(dict(firm_id=1, number='G-1', issued='2025-01-01', days=30, total='100',
                                ocr_supplier='Güneş Ticaret'))
        app.create_payment(dict(invoice_id=1, amount='20', paid_on='2025-01-02'))
        app.create_payment(dict(firm_id=1, amount='10', paid_on='2025-01-02'))
        app.create_account_entry(dict(kind='credit', firm_id=1, invoice_id=1,
                                      entry_date='2025-01-03', amount='5'))
        app.save_draft('invoice:1', {'fields':[{'name':'firm_id','value':'1'}]})
        with app.connect() as db:
            preview = app.firm_merge_details(db, 1, 2)
        self.assertEqual(preview['counts'], dict(invoices=1, openings=0, credits=1, payments=2))
        self.assertEqual(preview['balance'], 6500)
        app.merge_firms(1, 2, preview['token'])
        ledger = app.state()
        firm, = ledger['firms']
        self.assertEqual((firm['id'], firm['balance'], firm['default_due_days']), (2, 6500, 30))
        self.assertEqual(firm['phone'], '+90 555 123 45 67')
        self.assertIn('Güneş Ticaret', firm['ocr_aliases'])
        self.assertEqual({p['firm_id'] for p in ledger['payments']}, {2})
        self.assertEqual(ledger['credits'][0]['firm_id'], 2)
        self.assertEqual(app.load_draft('invoice:1')['payload']['fields'][0]['value'], '2')
        with app.sqlite3.connect(next(app.backup_directory().glob('*.sqlite3'))) as backup:
            self.assertEqual(backup.execute('SELECT firm_id FROM invoices').fetchone()[0], 1)

    def test_firm_merge_rejects_duplicate_invoices_and_stale_confirmation(self):
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Güneş Gıda Ltd.')")
        data = dict(firm_id=1, number='G-1', issued='2025-01-01', days=30, total='100')
        app.create_invoice(data)
        with app.connect() as db:
            preview = app.firm_merge_details(db, 1, 2)
        app.create_invoice(dict(data, firm_id=2, number='G-2'))
        with self.assertRaisesRegex(ValueError, 'Önizlemeyi'):
            app.merge_firms(1, 2, preview['token'])
        app.create_invoice(dict(data, firm_id=2, total='125'))
        with app.connect() as db:
            preview = app.firm_merge_details(db, 1, 2)
        self.assertEqual(preview['duplicates'], ['G-1'])
        with self.assertRaisesRegex(ValueError, 'Çakışan'):
            app.merge_firms(1, 2, preview['token'])
        self.assertEqual(len(app.state()['firms']), 2)

    def test_monthly_purchases_use_invoice_and_return_dates(self):
        with app.connect() as db:
            db.execute("INSERT INTO firms(name) VALUES('Yıldız Gıda')")
        app.create_invoice(dict(firm_id=1, number='G-1', issued='2024-12-31', days=30, total='100'))
        app.create_invoice(dict(firm_id=1, number='G-2', issued='2025-01-31', days=30, total='125'))
        app.create_invoice(dict(firm_id=2, number='Y-1', issued='2025-01-01', days=30, total='50'))
        app.create_invoice(dict(firm_id=2, number='Y-2', issued='2025-02-01', days=30, total='75'))
        app.create_payment(dict(firm_id=1, amount='20', paid_on='2025-01-02'))
        app.create_account_entry(dict(kind='credit', firm_id=1, invoice_id=1,
                                      entry_date='2025-01-03', amount='5'))
        app.create_account_entry(dict(kind='opening', firm_id=1, entry_date='2025-01-04',
                                      due='2025-02-01', amount='150'))
        summary = app.monthly_purchases('2025-01')
        self.assertEqual(summary['previous_month'], '2024-12')
        self.assertEqual(summary['totals'], dict(purchases=17500, returns=500, net=17000,
                                                previous_purchases=10000, previous_returns=0,
                                                previous_net=10000, change=7000, change_percent=70))
        first, second = summary['suppliers']
        self.assertEqual((first['net'], first['change_percent']), (12000, 20))
        self.assertIsNone(second['change_percent'])
        self.assertEqual(app.monthly_purchases('2023-01')['suppliers'], [])

    def test_range_report_uses_movement_dates_and_historical_debt(self):
        app.create_invoice(dict(firm_id=1, number='G-1', issued='2024-12-25', days=10, total='100'))
        app.create_invoice(dict(firm_id=1, number='G-2', issued='2025-01-10', days=30, total='60'))
        app.create_payment(dict(invoice_id=1, amount='20', paid_on='2024-12-31'))
        app.create_payment(dict(firm_id=1, amount='25', paid_on='2025-01-15'))
        app.create_account_entry(dict(kind='credit', firm_id=1, invoice_id=1,
                                      entry_date='2025-01-12', amount='5'))
        app.create_payment(dict(firm_id=1, amount='50', paid_on='2025-02-01'))
        with app.connect() as db:
            report = app.range_report(db, '2025-01-01', '2025-01-31', '2024-12-01', '2024-12-31')
        self.assertEqual((report['opening_debt'], report['closing_debt']), (8000, 11000))
        self.assertEqual(report['current']['purchases'], 6000)
        self.assertEqual(report['current']['returns'], 500)
        self.assertEqual(report['current']['payments'], 2500)
        self.assertEqual(report['previous']['payments'], 2000)
        firm, = report['suppliers']
        self.assertEqual((firm['overdue'], firm['due_today'], firm['future']), (5000, 0, 6000))
        self.assertEqual(app.state()['firms'][0]['balance'], 6000)

    def test_firm_payment_reduces_plan_without_changing_legacy_payments(self):
        first = dict(firm_id=1, number='G-1', issued='2025-01-01', days=5, total='100')
        app.create_invoice(first)
        app.create_invoice(dict(first, number='G-2', days=15, total='70'))
        app.create_payment(dict(invoice_id=1, amount='20', paid_on='2025-01-02'))
        payment = dict(firm_id=1, amount='120', paid_on='2025-01-03')
        app.create_payment(payment)
        overview = app.state(date(2025, 1, 10))
        self.assertEqual(overview['firms'][0]['balance'], 3000)
        self.assertEqual([entry['remaining'] for entry in overview['obligations']], [0, 3000])
        self.assertEqual([entry['paid'] for entry in overview['invoices']], [2000, 0])
        self.assertEqual(overview['totals']['overdue'], 0)
        self.assertEqual(overview['payments'][0]['target_type'], 'firm')
        self.assertIsNone(overview['payments'][0]['invoice_id'])
        with self.assertRaisesRegex(ValueError, 'cari borcunu aşamaz'):
            app.create_payment(dict(payment, amount='30.01'))
        app.create_payment(dict(payment, amount='110'), record_id=1)
        undo_id = app.delete_payment(dict(id=1, target_type='firm'))
        self.assertEqual(app.state()['firms'][0]['balance'], 15000)
        app.restore_deleted_record(undo_id)
        self.assertEqual(app.state()['firms'][0]['balance'], 4000)

    def test_restore_deleted_invoice_restores_children_once(self):
        app.create_invoice(dict(firm_id=1, number='G-1', issued='2025-01-01', days=10, total='100',
                                items=[dict(product='Çay', unit='adet', price='1.2345')]))
        app.create_payment(dict(invoice_id=1, amount='20', paid_on='2025-01-02'))
        app.create_account_entry(dict(kind='credit', firm_id=1, invoice_id=1,
                                      entry_date='2025-01-03', amount='10'))
        undo_id = app.delete_record('invoice', 1, dict(invoices=1, payments=1, items=1, entries=1))
        self.assertEqual(app.state()['totals']['total'], 0)
        app.restore_deleted_record(undo_id)
        restored = app.state()
        self.assertEqual(restored['totals']['total'], 7000)
        self.assertEqual(restored['items'][0]['unit_price'], '1.2345')
        with self.assertRaisesRegex(ValueError, 'bulunamadı'):
            app.restore_deleted_record(undo_id)

    def test_restore_payment_cannot_exceed_remaining_debt(self):
        app.create_invoice(dict(firm_id=1, number='G-1', issued='2025-01-01', days=10, total='100'))
        app.create_payment(dict(invoice_id=1, amount='70', paid_on='2025-01-02'))
        undo_id = app.delete_payment(dict(id=1))
        app.create_payment(dict(invoice_id=1, amount='40', paid_on='2025-01-03'))
        with self.assertRaisesRegex(ValueError, 'kalan borç'):
            app.restore_deleted_record(undo_id)
        self.assertEqual(app.state()['totals']['total'], 6000)

    def test_archived_firm_keeps_existing_debt_but_rejects_new_invoice(self):
        data = dict(firm_id=1, number='G-1', issued='2025-01-01', days=10, total='100')
        app.create_invoice(data)
        with app.connect() as db:
            db.execute('UPDATE firms SET archived=1 WHERE id=1')
        app.create_invoice(dict(data, days=15), record_id=1)
        app.create_payment(dict(invoice_id=1, amount='40', paid_on='2025-01-02'))
        with self.assertRaisesRegex(ValueError, 'arşivden çıkarın'):
            app.create_invoice(dict(data, number='G-2'))
        self.assertEqual(app.state(include_analysis=False)['totals']['total'], 6000)

    @patch('app.shutil.which', return_value=None)
    def test_ocr_engine_explains_missing_program(self, _which):
        with self.assertRaisesRegex(ValueError, 'kurulum|Kurulum|Mac-Uygulamasini'):
            app.ocr_engine()

    @patch('app.subprocess.run')
    @patch('app.shutil.which', return_value='/opt/homebrew/bin/tesseract')
    def test_ocr_engine_requires_turkish_language(self, _which, run):
        run.return_value.stdout = 'List of available languages\neng\n'
        with self.assertRaisesRegex(ValueError, 'Türkçe'):
            app.ocr_engine()

    @patch('app.subprocess.run')
    @patch('app.shutil.which', return_value='/opt/homebrew/bin/tesseract')
    def test_ocr_engine_selects_installed_languages(self, _which, run):
        run.return_value.stdout = 'List of available languages\ntur\neng\n'
        self.assertEqual(app.ocr_engine(), ('/opt/homebrew/bin/tesseract', 'tur+eng'))

    def invoice(self, **changes):
        data = dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',duplicate_confirmed=True,
                    items=[dict(product='Marka süt',unit='1 litre / adet',price='35.00')])
        data.update(changes)
        return app.create_invoice(data)

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
        self.assertEqual(app.state()['alerts'], [])
        self.assertEqual(len(app.state()['price_histories'][0]['points']), 2)

    def test_price_alerts_follow_whole_lira_boundary_in_both_directions(self):
        for index, price in enumerate(('4.10', '4.70', '4.9999', '5.00', '4.9999'), 1):
            self.invoice(number=f'F{index:03}', issued=f'2026-01-{index:02}',
                         items=[dict(product='Süt', unit='adet', price=price)])
        snapshot = app.state()
        self.assertEqual([row['number'] for row in snapshot['alerts']], ['F005', 'F004'])
        self.assertEqual(len(snapshot['price_histories'][0]['points']), 5)

    def test_invoice_price_change_count_matches_alerts_for_only_that_invoice(self):
        def products(milk, tea):
            return [dict(product='Süt', unit='adet', price=milk),
                    dict(product='Çay', unit='adet', price=tea)]

        self.invoice(items=products('9.10', '20.50'))
        second = self.invoice(number='F002', issued='2026-02-01', items=products('9.90', '21.00'))
        third = self.invoice(number='F003', issued='2026-02-02', items=products('10.00', '21.70'))
        snapshot = app.state()
        for record in snapshot['invoices']:
            count = sum(alert['invoice_id'] == record['id'] for alert in snapshot['alerts'])
            self.assertEqual(app.invoice_price_change_count(record['id']), count)
        self.assertEqual(app.invoice_price_change_count(second), 1)
        self.assertEqual(app.invoice_price_change_count(third), 1)

    def test_gift_quantity_does_not_change_paid_price_or_create_an_alert(self):
        items = [dict(product='Süt', unit='koli', price='220', quantity='10',
                      gift_quantity='1', row_number=1, line_total='2200', net='2200', vat='0')]
        invoice = dict(firm_id=1, number='F001', issued='2026-01-01', days=30, total='2200',
                       items=items, import_check=dict(expected_rows=[1], line_total_kind='net'))
        app.create_invoice(invoice)
        without_gift = dict(items[0], gift_quantity='')
        app.create_invoice(dict(invoice, number='F002', issued='2026-01-02', items=[without_gift]))
        snapshot = app.state()
        self.assertEqual(snapshot['alerts'], [])
        self.assertEqual([row['gift_quantity'] for row in snapshot['items']], ['1', '0'])
        self.assertEqual(snapshot['totals']['total'], 440000)
        new_price = dict(without_gift, price='230', line_total='2300', net='2300')
        app.create_invoice(dict(invoice, number='F003', issued='2026-01-03', total='2300', items=[new_price]))
        alert, = app.state()['alerts']
        self.assertEqual((alert['old_price'], alert['price']), (22000, 23000))

    def test_separate_gift_line_counts_toward_coverage_but_not_price_history(self):
        paid = dict(product='Süt', unit='koli', price='220', quantity='10',
                    row_number=1, line_total='2200', net='2200', vat='0')
        gift = dict(paid, gift_only=True, gift_quantity='1', price='0', quantity='0',
                    row_number=2, line_total='0', net='0')
        invoice = dict(firm_id=1, number='F001', issued='2026-01-01', days=30, total='2200',
                       items=[paid, gift], import_check=dict(expected_rows=[1, 2], line_total_kind='net'))
        app.create_invoice(invoice)
        app.create_invoice(dict(invoice, number='F002', issued='2026-01-02',
                                items=[paid], import_check=dict(expected_rows=[1], line_total_kind='net')))
        snapshot = app.state()
        gift_row, = [row for row in snapshot['items'] if row['gift_only']]
        self.assertEqual((gift_row['price'], gift_row['gift_quantity']), (0, '1'))
        self.assertEqual(snapshot['totals']['total'], 440000)
        self.assertEqual(snapshot['alerts'], [])
        self.assertEqual([point['price'] for point in snapshot['price_histories'][0]['points']], [22000, 22000])
        with self.assertRaisesRegex(ValueError, 'bedelsiz'):
            app.create_invoice(dict(invoice, items=[paid, dict(gift, gift_quantity='0')]), record_id=1)

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
        self.invoice(number='F002',issued='2026-02-01',items=[dict(product='Marka süt',unit='1 litre / adet',price='36.01')])
        alert=app.state()['alerts'][0]
        self.assertFalse(alert['approved'])
        with app.connect() as db:
            db.execute('INSERT INTO price_reviews VALUES(?,?)',(alert['fingerprint'],'2026-02-01'))
        self.assertTrue(app.state()['alerts'][0]['approved'])
        app.create_invoice(dict(firm_id=1,number='F001',issued='2026-01-20',days=20,total='1000.50',items=[dict(product='Marka süt',unit='1 litre / adet',price='37')]),1)
        changed=app.state()['alerts'][0]
        self.assertFalse(changed['approved'])
        self.assertLess(changed['change'],0)

    def test_pdf_text_layer_preserves_exact_product_name_code_and_amounts(self):
        words = []
        def add(text, left, top, width):
            words.append(dict(text=text,left=left,top=top,width=width,height=12))
        add('1',50,100,20); add('3532900',86,100,65)
        add('BENİMO',190,100,55); add('ÇİKOLATALI',250,100,90); add('80GX18KL',345,100,64)
        add('18',416,100,22); add('Adet',450,100,35); add('27,7200',512,100,58)
        add('%1,00',714,100,45); add('4,97',790,100,47); add('497,16',890,100,66)

        readings, _ = app.pdf_text_readings([{'width':1000,'height':1400,'words':words}], [])

        item = readings[0]['items'][0]
        self.assertEqual(item['product'], 'BENİMO ÇİKOLATALI 80GX18KL')
        self.assertEqual(item['code'], '3532900')
        self.assertEqual((item['quantity'], item['line_total'], item['vat']), ('18', '497.16', '4.97'))

    def test_desktop_launcher_uses_separate_mac_and_windows_data_folders(self):
        home = Path('/Users/sancakli')
        self.assertEqual(
            storage_directory('darwin', home),
            home / 'Library' / 'Application Support' / 'Sancakli Market',
        )
        self.assertEqual(
            storage_directory('win32', Path('C:/Users/Sancakli'), 'D:/LocalData'),
            Path('D:/LocalData') / 'Sancakli Market',
        )

    def test_desktop_resources_use_pyinstaller_bundle_directory(self):
        bundle = Path(self.tmp.name) / 'bundle'
        with patch.object(sys, '_MEIPASS', str(bundle), create=True):
            self.assertEqual(resource_path('static/index.html'), bundle/'static/index.html')

    def test_istanbul_clock_has_permanent_utc_plus_three_offset(self):
        self.assertEqual(app.ISTANBUL.utcoffset(datetime(2026, 1, 1)), app.timedelta(hours=3))
        self.assertEqual(app.ISTANBUL.utcoffset(datetime(2026, 7, 1)), app.timedelta(hours=3))

    def test_windowed_server_logging_tolerates_missing_stderr(self):
        with patch.object(sys, 'stderr', None):
            app.Handler.log_message(object(), 'GET %s', '/')

    def test_windows_launcher_uses_installed_turkish_ocr_data(self):
        with tempfile.TemporaryDirectory() as directory:
            language_directory = Path(directory) / 'Sancakli Market' / 'tessdata'
            language_directory.mkdir(parents=True)
            with patch.object(sys, 'platform', 'win32'), patch.dict(
                os.environ, {'LOCALAPPDATA': directory, 'PATH': ''}, clear=False
            ):
                os.environ.pop('TESSDATA_PREFIX', None)
                add_ocr_paths()
                self.assertEqual(os.environ['TESSDATA_PREFIX'], str(language_directory))

    def test_form_drafts_are_saved_restored_and_cleared(self):
        payload = {'fields': [{'name': 'amount', 'value': '9000'}], 'items': []}
        app.save_draft('entry:new', payload)
        self.assertEqual(app.load_draft('entry:new')['payload'], payload)
        app.clear_draft('entry:new')
        self.assertIsNone(app.load_draft('entry:new'))
        with self.assertRaisesRegex(ValueError, 'Taslak türü'):
            app.save_draft('../market', payload)


if __name__ == '__main__':
    unittest.main()
