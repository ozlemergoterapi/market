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
        self.assertEqual(draft['items'][0]['price'],'26.3789')
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
        self.assertEqual(draft['items'][0]['price'],'10.1000')
        self.assertFalse(any('uyuşmuyor' in w for w in draft['warnings']))

    def test_invalid_date_is_left_blank(self):
        self.assertEqual(parse_invoice('Fatura Tarihi: 31.02.2026')['issued'],'')
