import unittest
from product_matching import compare_items, compare_suppliers, compare_package_prices, identity, pair_key


def item(id, name, code='', unit='adet', firm=1, price=3500):
    return dict(id=id,invoice_id=id,firm_id=firm,product=name,code=code,unit=unit,price=price)


class MatchingTests(unittest.TestCase):
    def test_package_comparison_shows_increase_when_size_shrinks_at_same_price(self):
        before = dict(item(1, 'Örnek süt ürünü 500 g'), issued='2026-11-01', unit_price='50')
        after = dict(item(2, 'Örnek süt ürünü 450 g'), issued='2026-11-15', unit_price='50')
        result = compare_package_prices([after, before])
        self.assertEqual(result['unit'], 'kg')
        self.assertEqual([row['normalized_price'] for row in result['rows']], ['100.0000', '111.1111'])
        self.assertEqual(result['percent'], '11.11')
        packaged = dict(item(2, 'Örnek süt ürünü 12x450 g', unit='koli'), issued='2026-11-15', unit_price='600')
        self.assertEqual(compare_package_prices([before, packaged])['rows'][1]['normalized_price'], '111.1111')
        with self.assertRaisesRegex(ValueError, 'Ağırlık ve hacim'):
            compare_package_prices([before, dict(after, product='Örnek ürün 450 ml')])
    def test_supplier_comparison_uses_latest_purchase_and_normalizes_case_size(self):
        values = [
            dict(item(1, "Örnek Süt 1 L 12'li Koli", 'A12', 'koli', firm=1),
                 firm='Güneş', issued='2026-11-01', unit_price='108'),
            dict(item(2, "Örnek Süt 12x1 L", 'A12', 'koli', firm=1),
                 firm='Güneş', issued='2026-11-10', unit_price='144'),
            dict(item(3, 'Örnek Süt 1000 ML X24', 'B24', 'koli', firm=2),
                 firm='Yıldız', issued='2026-11-04', unit_price='252'),
            dict(item(4, 'Örnek Süt 1L', 'C1', firm=3),
                 firm='Ufuk', issued='2026-11-03', unit_price='11'),
        ]
        group, = compare_suppliers(values)
        self.assertEqual(group['unit'], 'adet')
        self.assertEqual(group['label'], 'Örnek Süt 1 L')
        self.assertEqual([row['id'] for row in group['suppliers']], [3, 4, 2])
        self.assertEqual([row['common_price'] for row in group['suppliers']],
                         ['10.500000', '11.000000', '12.000000'])
        self.assertTrue(group['suppliers'][0]['lowest'])

    def test_supplier_comparison_keeps_different_sizes_and_ambiguous_units_separate(self):
        values = [
            dict(item(1, 'Örnek Süt 1 L', 'ORTAK', firm=1), firm='Güneş', issued='2026-11-01', unit_price='10'),
            dict(item(2, 'Örnek Süt 500 ML', 'ORTAK', firm=2), firm='Yıldız', issued='2026-11-01', unit_price='5'),
            dict(item(3, 'Örnek Süt 1 L', 'ORTAK', 'koli', firm=3), firm='Ufuk', issued='2026-11-01', unit_price='50'),
            dict(item(4, 'Örnek Süt 1 L x24', 'ORTAK', 'koli 12 adet', firm=4), firm='İnci', issued='2026-11-01', unit_price='60'),
        ]
        self.assertEqual(compare_suppliers(values), [])

    def test_same_code_matches_name_ocr_variation(self):
        old=item(1,'BENİMO KOMBO KREMALI 96GX18 KL','8223298')
        new=item(2,'BENIMO K0MBO KREMALl 96G X18 KL','8223298',price=3900)
        matches,suggestions,_=compare_items([old,new])
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['matched_by'],'Ürün kodu')
        self.assertFalse(suggestions)

    def test_same_name_different_size_is_not_merged(self):
        old=item(1,'MARKA SUT 1L','12345')
        new=item(2,'MARKA SUT 500ML','12345')
        matches,suggestions,_=compare_items([old,new])
        self.assertFalse(matches)
        self.assertEqual(len(suggestions),1)

    def test_code_less_ocr_name_needs_confirmation_and_is_remembered(self):
        old=item(1,'BENIMO KOMBO KREMALI 96GX18')
        new=item(2,'BENIMO K0MBO KREMALI 96GX18')
        matches,suggestions,_=compare_items([old,new])
        self.assertFalse(matches)
        self.assertEqual(len(suggestions),1)
        review=dict(left_key=identity(old),right_key=identity(new),decision='same')
        matches,suggestions,_=compare_items([old,new], [review])
        self.assertEqual(len(matches),1)
        self.assertFalse(suggestions)

    def test_different_product_decision_does_not_generate_price_alert(self):
        old=item(1,'GONG PEYNIRLI 34GX18')
        new=item(2,'GONG BAHARATLI 34GX18')
        decision=dict(left_key=identity(old),right_key=identity(new),decision='different')
        matches,suggestions,unmatched=compare_items([old,new],[decision])
        self.assertFalse(matches)
        self.assertFalse(suggestions)
        self.assertEqual(len(unmatched),1)

    def test_other_supplier_and_unit_do_not_auto_match(self):
        old=item(1,'SUT 1L','12345')
        new=item(2,'SUT 1L','12345',firm=2)
        self.assertFalse(compare_items([old,new])[0])
        new=item(2,'SUT 1L','12345',unit='koli')
        matches,_,unmatched=compare_items([old,new])
        self.assertFalse(matches)
        self.assertEqual(len(unmatched),1)

    def test_different_codes_are_not_auto_merged_by_same_name(self):
        old=item(1,'SUT 1L','12345')
        new=item(2,'SUT 1L','67890')
        matches,suggestions,_=compare_items([old,new])
        self.assertFalse(matches)
        self.assertEqual(len(suggestions),1)

    def test_latest_matching_invoice_is_compared(self):
        values=[item(1,'SUT 1L','12345',price=3000),item(2,'SUT 1L','12345',price=3500),item(3,'SUT 1L','12345',price=3900)]
        matches,_,_=compare_items(values)
        self.assertEqual(matches[-1]['previous']['price'],3500)
