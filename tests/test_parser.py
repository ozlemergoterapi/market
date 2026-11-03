import unittest
from invoice_parser import parse_invoice


class ParserTests(unittest.TestCase):
    def test_turkish_invoice_with_overlap_discount_and_wrapped_name(self):
        text = '''KÖSEOĞLU GIDA VE MEŞRUBAT PAZ.LTD.ŞTİ
Belge No: E102026000001584
Fatura Tarihi: 22-10-2026
1 8223298 BENİMO KOMBO KREMALI
96GX18 KL 18 Adet 27,7200 TL % 1,00 4,69 TL 470,13 TL
2 2244700 GONG BAHARATLI PEYNİRLİ 34GX18 KL 18 Adet 14,2600 TL % 1,00 2,42 TL 241,85 TL
1 8223298 BENİMO KOMBO KREMALI
96GX18 KL 18 Adet 27,7200 TL % 1,00 4,69 TL 470,13 TL
Mal / Hizmet Toplam Tutarı 12.808,77 TL
Toplam İskonto (%5,78) 739,73 TL
Ödenecek Tutar 12.189,73 TL'''
        draft = parse_invoice(text,[dict(id=7,name='Köseoğlu Eti')])
        self.assertEqual(draft['firm_id'],7)
        self.assertEqual(draft['number'],'E102026000001584')
        self.assertEqual(draft['issued'],'2026-10-22')
        self.assertEqual(draft['total'],'12189.73')
        self.assertEqual(len(draft['items']),2)
        self.assertEqual(draft['items'][0]['price'],'26.38')
        self.assertIn('96GX18',draft['items'][0]['product'])
        self.assertTrue(any('uyuşmuyor' in w for w in draft['warnings']))

    def test_missing_information_is_not_invented(self):
        draft = parse_invoice('Bulanık fotoğraf\nToplam İskonto 739,73 TL')
        self.assertEqual(draft['issued'],'')
        self.assertEqual(draft['total'],'')
        self.assertEqual(draft['number'],'')
        self.assertIsNone(draft['firm_id'])
        self.assertEqual(draft['items'],[])

    def test_small_complete_invoice(self):
        draft = parse_invoice('''Fatura Tarihi 03.11.2026
1 1234567 SÜT 1L 2 Adet 10,0000 TL % 1,00 0,20 TL 20,00 TL
Ödenecek Tutar 20,20 TL''')
        self.assertEqual(draft['items'][0]['price'],'10.10')
        self.assertFalse(any('uyuşmuyor' in w for w in draft['warnings']))

    def test_invalid_date_is_left_blank(self):
        self.assertEqual(parse_invoice('Fatura Tarihi: 31.02.2026')['issued'],'')

    def test_separate_labels_and_values_with_ocr_noise(self):
        draft=parse_invoice("""KOSEOĞLU GİDA VE MEŞRUBAT PAZ LTD.ŞTİ >. = b4
Belge No:
E10 2026 000001584
Fatura Tarihi:
22 - 10 - 2026
Ödenecek Tutar:
12.189, 73 TL""",[dict(id=4,name='Köseoğlu Eti Bisküvi')])
        self.assertEqual(draft['firm_id'],4)
        self.assertEqual(draft['number'],'E102026000001584')
        self.assertEqual(draft['issued'],'2026-10-22')
        self.assertEqual(draft['total'],'12189.73')
        self.assertIn('12.189,73',draft['sources']['total'])

    def test_does_not_take_discount_or_tax_as_missing_total(self):
        draft=parse_invoice('Ödenecek Tutar:\nToplam İskonto 739,73 TL\nKDV 120,69 TL')
        self.assertEqual(draft['total'],'')

    def test_conflicting_totals_and_firms_stay_unselected(self):
        draft=parse_invoice('KOSEOGLU GIDA LTD\nÖdenecek Tutar 100,00 TL\nÖdenecek Tutar 200,00 TL',
                            [dict(id=1,name='Köseoğlu Eti'),dict(id=2,name='Köseoğlu Toptan')])
        self.assertEqual(draft['total'],'')
        self.assertIsNone(draft['firm_id'])

    def test_no_number_digits_are_invented(self):
        self.assertEqual(parse_invoice('Belge No: E10202600001584')['number'],'')

    def test_damaged_decimal_is_not_treated_as_smaller_total(self):
        self.assertEqual(parse_invoice('Ödenecek Tutar: 12.189,7B TL')['total'],'')

    def test_product_pack_size_is_not_invoice_quantity(self):
        draft=parse_invoice('1 8223298 BENIMO KOMBO KREMALI 96GX24 KL 18Adet 27,7200 TL % 1,00 4,69 TL 470,13 TL')
        self.assertEqual(draft['items'][0]['quantity'],'18')
        self.assertIn('96GX24',draft['items'][0]['product'])

    def test_product_description_number_is_not_a_row_number(self):
        draft=parse_invoice('''1 8223298 HOSBES MUZLU KR.GOF.
120GX20KL 10 Adet 47,5200 TL % 1,00 4,48 TL 447,76 TL''')
        self.assertEqual(draft['import_check']['expected_rows'],[1])
        self.assertEqual(len(draft['items']),1)

    def test_fractional_and_grouped_quantities(self):
        for quantity,net,expected in [('1,25','1,25','1.25'),('1.000','1.000,00','1000')]:
            with self.subTest(quantity=quantity):
                draft=parse_invoice(f'1 1234567 URUN 200G {quantity} Kg 1,0000 TL % 0,00 0,00 TL {net} TL')
                self.assertEqual(draft['items'][0]['quantity'],expected)

    def test_suspicious_quantity_requires_review(self):
        draft=parse_invoice('1 1234567 URUN 200G 180 Adet 27,7200 TL % 1,00 4,69 TL 470,13 TL')
        self.assertTrue(draft['items'][0]['review'])

    def test_garbled_quantity_is_not_guessed(self):
        draft=parse_invoice('1 1234567 URUN 200G I8 Adet 27,7200 TL % 1,00 4,69 TL 470,13 TL')
        self.assertEqual(draft['items'],[])
