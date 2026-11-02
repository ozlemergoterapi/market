import unittest
from invoice_parser import check_items, parse_invoice, merge_readings


class CoverageTests(unittest.TestCase):
    def test_rounded_unit_price_can_reconcile_with_exact_line_total(self):
        row=dict(row_number=1,quantity='18',price='26.38',line_total='474.82')
        self.assertEqual(check_items([row],'474.82',dict(expected_rows=[1]))['errors'],[])

    def test_missing_row_and_missing_total_are_not_success(self):
        row=dict(row_number=1,quantity='18',price='26.38',line_total='474.82')
        errors=check_items([row],'719.09',dict(expected_rows=[1,2]))['errors']
        self.assertTrue(any('Eksik' in e for e in errors))
        self.assertTrue(any('toplam' in e for e in errors))

    def test_wrong_quantity_and_duplicate_rows_are_detected(self):
        row=dict(row_number=1,quantity='180',price='26.38',line_total='474.82')
        errors=check_items([row,row],'949.64',dict(expected_rows=[1,2]))['errors']
        self.assertTrue(any('miktarı' in e for e in errors))
        self.assertTrue(any('birden fazla' in e for e in errors))

    def test_gap_in_detected_invoice_rows_is_reported(self):
        draft=parse_invoice('''1 1234567 SUT 1L 2 Adet 10,0000 TL % 1,00 0,20 TL 20,00 TL
3 1234568 SU 1L 2 Adet 10,0000 TL % 1,00 0,20 TL 20,00 TL
Ödenecek Tutar 40,40 TL''')
        self.assertEqual(draft['import_check']['expected_rows'],[1,2,3])
        self.assertTrue(any('2' in e and 'Eksik' in e for e in draft['coverage']['errors']))

    def test_complete_products_are_not_discarded_for_better_headers(self):
        rows=parse_invoice('1 1234567 SUT 1L 2 Adet 10,0000 TL % 1,00 0,20 TL 20,00 TL')
        header=parse_invoice('Belge No: E102026000001584\nFatura Tarihi: 22-10-2026\nÖdenecek Tutar 20,20 TL')
        merged=merge_readings([rows,header])
        self.assertEqual(len(merged['items']),1)
        self.assertEqual(merged['number'],'E102026000001584')
        self.assertEqual(merged['coverage']['errors'],[])

    def test_conflicting_readings_do_not_silently_pick_total(self):
        a=parse_invoice('Ödenecek Tutar 20,20 TL')
        b=parse_invoice('Ödenecek Tutar 200,20 TL')
        self.assertEqual(merge_readings([a,b])['total'],'')
