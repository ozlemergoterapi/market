import unittest
from product_matching import compare_items, identity, pair_key


def item(id, name, code='', unit='adet', firm=1, price=3500):
    return dict(id=id,invoice_id=id,firm_id=firm,product=name,code=code,unit=unit,price=price)


class MatchingTests(unittest.TestCase):
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
