import unittest
from decimal import Decimal

from invoice_parser import merge_readings, parse_invoice


# Supplier/product evidence only. The invoice customer's personal details are excluded.
ROWS = [
    (1, '8223298', 'BENİMO KOMBO KREMALI 96GX18 KL', 18, '27.7200', '4.69', '470.13'),
    (2, '2244700', 'GONG BAHARATLI PEYNİRLİ 34GX18 KL', 18, '14.2600', '2.42', '241.85'),
    (3, '2248200', 'GONG BALLI HARDALLI 34GX18 KL', 18, '14.2600', '2.42', '241.85'),
    (4, '2246700', 'GONG 40GX18 DP', 18, '14.2600', '2.42', '241.86'),
    (5, '7815429', 'B25 GONG SÜTLÜ KAKAO KAPLAMALI 30GX24', 24, '19.8000', '4.48', '447.76'),
    (6, '1361200', 'TOPKEK MEY. BSZ 40GX24 TV', 24, '10.3000', '2.33', '232.92'),
    (7, '1361300', 'TOPKEK KAK. BSZ 35GX24 TV', 24, '10.3000', '2.33', '232.92'),
    (8, '4637300', 'BROWNI GOLD KAKAOLU 45GX24 KL', 24, '14.2600', '3.22', '322.48'),
    (9, '1637400', 'BROWNI GOLD VİŞNELİ 45GX24 KL', 24, '14.2600', '3.22', '322.48'),
    (10, '1738700', 'POP. VIS. 60GX24 KL', 24, '14.2600', '3.22', '322.48'),
    (11, '4540800', 'CRAX ACILI 50GX20', 20, '11.8800', '2.24', '223.88'),
    (12, '2744900', 'CRAX CHİLLİ LİME 50GX20', 20, '19.8000', '3.73', '373.13'),
    (13, '21107000', 'TUTKU BOLD', 12, '34.0600', '3.85', '385.12'),
    (14, '4431800', 'NERO 110GX18 KL', 18, '23.7600', '4.03', '402.98'),
    (15, '4933600', 'BUR. KREMALI 100GX12 DP', 12, '19.8000', '2.24', '223.88'),
    (16, '1232800', 'BURÇAK SÜTLÜ KAKAO KREMALI 100GX12', 12, '19.8000', '2.24', '223.88'),
    (17, '2752000', 'HOSBES KAK. 142GX20 KL', 20, '47.5200', '8.96', '895.51'),
    (18, '2655300', 'HOSBES HC. 142GX20 DP', 10, '47.5200', '4.48', '447.76'),
    (19, '1452300', 'HOSBES MUZLU KR.GOF. 120GX20KL', 10, '47.5200', '4.48', '447.76'),
    (20, '3352600', 'HOSBES CIL. 142GX20 KL', 10, '47.5200', '4.48', '447.76'),
    (21, '1852000', 'HOSBES KAK. 40GX24 DP', 24, '15.8400', '3.58', '358.21'),
    (22, '4163500', 'PAYKEK MOZ. 200GX6 KL', 6, '49.1100', '2.78', '277.64'),
    (23, '1310100', 'P.BEURRE 400GX10 KL', 5, '57.8200', '2.72', '272.40'),
    (24, '4754100', 'KARAM GURME 50GX18 KL', 36, '23.7600', '8.06', '805.96'),
    (25, '7712272', 'KARAM GURME SÜTLÜ 50GX18', 36, '23.7600', '8.06', '805.96'),
    (26, '1018200', 'PETITO GOFRET 26GX24', 24, '10.6900', '2.42', '241.74'),
    (27, '9671822', 'SÜT BURGER KAKOLU 35X16', 32, '15.4500', '4.66', '465.85'),
    (28, '1268900', 'SUT BURGER 35GX16TV', 32, '15.4500', '4.66', '465.85'),
    (29, '1513804', 'B26 SÜT BURGER YABAN M. YOĞ 35GX16TV', 16, '15.4500', '2.33', '232.92'),
    (30, '1268400', 'SUT BURGER ÇİLEK 35GX16TV', 16, '15.4500', '2.33', '232.92'),
    (31, '2993651', 'ETİ CİK BOL SÜTLÜ MİNİ 32GX12X12', 24, '33.6600', '7.61', '761.19'),
    (32, '40005744', 'STANT KASAÜSTÜ MINI TABLET 2026', 1, '0.0100', '0.00', '0.01'),
]


def tr(value):
    return str(value).replace('.', ',')


def row_text(row):
    number, code, product, quantity, raw_price, vat, net = row
    rate = '0,00' if number == 32 else '1,00'
    return f'{number} {code} {product} {quantity} Adet {tr(raw_price)} TL % {rate} {tr(vat)} TL {tr(net)} TL'


class FullInvoiceTests(unittest.TestCase):
    def test_real_32_line_table_keeps_all_rows_despite_page_overlap(self):
        first_page = '\n'.join(row_text(row) for row in ROWS[:24])
        second_photo = '\n'.join(row_text(row) for row in ROWS[1:])
        text = first_page + '\n' + second_photo + '\nÖdenecek Tutar 12.189,73 TL'

        draft = parse_invoice(text)

        self.assertEqual(len(draft['items']), 32)
        self.assertEqual(draft['import_check']['expected_rows'], list(range(1, 33)))
        self.assertEqual(draft['total'], '12189.73')
        self.assertEqual(draft['coverage']['errors'], [])
        self.assertEqual(
            [(item['row_number'], item['code'], item['quantity']) for item in draft['items']],
            [(number, code, str(Decimal(quantity))) for number, code, _, quantity, *_ in ROWS],
        )
        self.assertEqual(draft['items'][0]['price'], '26.38')
        self.assertEqual(draft['items'][-1]['line_total'], '0.01')

    def test_complementary_layouts_are_merged_by_row_number(self):
        first = parse_invoice(row_text(ROWS[0]) + '\n' + row_text(ROWS[2]))
        second = parse_invoice(row_text(ROWS[1]) + '\n' + row_text(ROWS[2]) + '\nÖdenecek Tutar 963,36 TL')

        merged = merge_readings([first, second])

        self.assertEqual([item['row_number'] for item in merged['items']], [1, 2, 3])
        self.assertEqual(merged['import_check']['expected_rows'], [1, 2, 3])
        self.assertEqual(merged['coverage']['errors'], [])

    def test_conflicting_layout_row_is_not_selected(self):
        correct = parse_invoice(row_text(ROWS[0]) + '\nÖdenecek Tutar 474,82 TL')
        changed = list(ROWS[0])
        changed[3] = 180
        conflicting = parse_invoice(row_text(tuple(changed)) + '\nÖdenecek Tutar 474,82 TL')

        merged = merge_readings([correct, conflicting])

        self.assertEqual(merged['items'], [])
        self.assertTrue(any('1. ürün satırı' in warning and 'çelişkili' in warning for warning in merged['warnings']))
        self.assertTrue(any('Eksik fatura kalemleri: 1.' in error for error in merged['coverage']['errors']))

    def test_repeated_matching_layouts_win_over_one_damaged_reading(self):
        correct = parse_invoice(row_text(ROWS[0]) + '\nÖdenecek Tutar 474,82 TL')
        changed = list(ROWS[0])
        changed[3] = 180
        damaged = parse_invoice(row_text(tuple(changed)) + '\nÖdenecek Tutar 474,82 TL')

        merged = merge_readings([correct, correct, damaged])

        self.assertEqual([item['row_number'] for item in merged['items']], [1])
        self.assertTrue(any('birden fazla aynı çıkan' in warning for warning in merged['warnings']))

    def test_conflicting_overlap_inside_one_reading_is_not_hidden(self):
        changed = list(ROWS[0])
        changed[6] = '470.12'
        text = row_text(ROWS[0]) + '\n' + row_text(tuple(changed)) + '\nÖdenecek Tutar 474,82 TL'

        draft = parse_invoice(text)

        self.assertEqual(draft['items'], [])
        self.assertTrue(any('1. ürün satırı' in warning and 'çelişkili' in warning for warning in draft['warnings']))
        self.assertTrue(any('Eksik fatura kalemleri: 1.' in error for error in draft['coverage']['errors']))


if __name__ == '__main__':
    unittest.main()
