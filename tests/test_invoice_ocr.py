import unittest

from app import apply_supplier_layout, tsv_reading, verified_pdf_draft
from invoice_parser import merge_readings
from invoice_ocr import parse_tsv_table, _clean_product


HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


class TsvBuilder:
    def __init__(self):
        self.rows = []
        self.word = 0

    def add(self, text, left, top, width=None, confidence=94, block=1, line=1, page=1):
        self.word += 1
        width = width if width is not None else max(len(text) * 8, 8)
        self.rows.append(
            f"5\t{page}\t{block}\t1\t{line}\t{self.word}\t{left}\t{top}\t{width}\t12\t{confidence}\t{text}\n"
        )

    def item(self, number, code, name, quantity, raw, vat, net, top, wrap=False, confidence=94,
             ordinal_text=None, unit_above_quantity=False):
        # Use unrelated block/line ids to reproduce Tesseract's table-column output.
        self.add(ordinal_text or str(number), 50, top, 20, confidence, block=1, line=number)
        self.add(code, 86, top, 65, confidence, block=2, line=number + 100)
        words = name.split()
        split = max(1, len(words) // 2) if wrap else len(words)
        x = 190
        for word in words[:split]:
            self.add(word, x, top, confidence=confidence, block=3, line=number + 200)
            x += max(len(word) * 8, 8) + 5
        if wrap:
            x = 190
            for word in words[split:]:
                self.add(word, x, top + 14, confidence=confidence, block=9, line=number + 500)
                x += max(len(word) * 8, 8) + 5
        self.add(str(quantity), 416, top, 22, confidence, block=4, line=number + 300)
        self.add("Adet", 450, top - 2 if unit_above_quantity else top, 35, confidence, block=8, line=number + 700)
        self.add(raw, 512, top, 58, confidence, block=5, line=number + 400)
        self.add("%1,00", 714, top, 45, confidence, block=5, line=number + 401)
        self.add(vat, 790, top, 47, confidence, block=6, line=number + 600)
        self.add(net, 890, top, 66, confidence, block=7, line=number + 800)

    def text(self):
        return HEADER + "".join(self.rows)


class InvoiceTsvTests(unittest.TestCase):
    def test_compact_brand_table_preserves_pack_size_and_product_code(self):
        for code_header, code in [('Ürün', 'ABC-001'), ('Barkod', '8691234567890')]:
            with self.subTest(code_header=code_header):
                table = TsvBuilder()
                for value, left in [('S.N', 142), ('Marka', 260), (code_header, 384),
                                    ('Mal/Hizmet', 908), ('Miktar', 1333), ('Birim', 1403),
                                    ('Fiyat', 1554), ('KDV', 1695), ('KDV', 1790),
                                    ('Tutarı', 1835), ('Tutar', 1963)]:
                    table.add(value, left, 100)
                if code_header == 'Ürün':
                    table.add('Kodu', 446, 100)
                for value, left in [('1', 165), ('ÖRNEK', 260), (code, 387), ('Bisküvi', 604),
                                    ('X', 905), ('24', 935), ('Ad', 970), ('2', 1357),
                                    ('Koli', 1404), ('20,00', 1505), ('%10,00', 1695),
                                    ('4,00', 1820), ('40,00', 1960)]:
                    table.add(value, left, 150)

                result = parse_tsv_table(table.text())

                self.assertEqual(result['labeled_pages'], 1)
                self.assertEqual(result['expected_rows'], [1])
                row = result['items'][0]
                self.assertEqual((row['code'], row['product'], row['quantity'], row['unit'], row['net'], row['vat']),
                                 (code, 'Bisküvi X 24 Ad', '2', 'koli', '40.00', '4.00'))

    def test_multiline_product_code_table_rejoins_separate_cells(self):
        table = TsvBuilder()
        table.add('Sıra', 140, 94, width=30)
        for value, left in [('Ürün', 205), ('Kodu', 281), ('Mal', 350), ('Hizmet', 405),
                            ('Miktar', 776), ('Birim', 933), ('Fiyat', 1008), ('KDV', 1122),
                            ('Oranı', 1181), ('KDV', 1270), ('Tutarı', 1325), ('Diğer', 1483)]:
            table.add(value, left, 100)
        for value, left, top in [('ERP', 205, 160), ('STK-', 240, 160), ('000123', 205, 174),
                                 ('Meyve', 350, 160), ('Suyu', 400, 160), ('X', 350, 174),
                                 ('12', 370, 174), ('Ad', 395, 174), ('1', 150, 167),
                                 ('2', 850, 167), ('Koli', 875, 167), ('20,00', 975, 160),
                                 ('4,00', 1360, 167), ('40,00', 1930, 167)]:
            table.add(value, left, top)

        result = parse_tsv_table(table.text())

        self.assertEqual(result['expected_rows'], [1])
        row = result['items'][0]
        self.assertEqual((row['code'], row['product'], row['source_price']),
                         ('ERP STK-000123', 'Meyve Suyu X 12 Ad', '20'))
        positioned = tsv_reading(table.text())
        header = dict(number='TEST123', issued='2026-11-26', total='44.00',
                      items=[dict(row_number=1, code='', product='Eksik ad')])
        draft = verified_pdf_draft([positioned, header])
        self.assertEqual(draft['items'][0]['product'], 'Meyve Suyu X 12 Ad')
        self.assertFalse(draft['coverage']['errors'])
        self.assertIsNone(verified_pdf_draft([positioned, dict(header, total='45.00')]))
        self.assertIsNone(verified_pdf_draft([dict(positioned, table_pages=2), header]))

    def test_reads_seller_code_and_wrapped_description_in_e_invoice(self):
        table = TsvBuilder()
        for label, left in [('Sıra', 143), ('Mal', 230), ('Hizmet', 290),
                            ('Satıcı', 444), ('Üretici', 650), ('Miktar', 846),
                            ('Birim', 950), ('İskonto', 1111), ('KDV', 1360),
                            ('KDV', 1536), ('Diğer', 1741), ('Mal', 1877), ('Hizmet', 1938)]:
            table.add(label, left, 100)
        for number, top, name in [(1, 190, 'Peynir'), (2, 320, 'Yoğurt')]:
            table.add(str(number), 154, top)
            table.add(name, 205, top - 35)
            table.add('X', 205, top + 35)
            table.add('8', 227, top + 35)
            table.add(f'01.02.000{number}', 447, top)
            table.add(f'900000{number}', 660, top)
            table.add('10', 868, top)
            table.add('Adet', 894, top)
            table.add('20,00', 975, top)
            table.add('2,00', 1630, top)
            table.add('200,00', 1955, top)
        table.add('Toplam', 1580, 400)
        table.add('Not:', 205, 435)

        result = parse_tsv_table(table.text())

        self.assertEqual(result['labeled_pages'], 1)
        self.assertEqual(result['expected_rows'], [1, 2])
        self.assertEqual(result['warnings'], [])
        self.assertEqual([(row['product'], row['code'], row['quantity'], row['net'], row['vat'])
                          for row in result['items']],
                         [('Peynir X 8', '01.02.0001', '10', '200.00', '2.00'),
                          ('Yoğurt X 8', '01.02.0002', '10', '200.00', '2.00')])

    def test_supplier_layout_reads_net_before_vat_and_reconciles_total(self):
        table = TsvBuilder()
        table.item(1, "1111111", "Çay", 10, "10,00", "100,00", "8,00", 100)
        table.item(2, "2222222", "Şeker", 5, "10,00", "50,00", "4,00", 150)
        draft = merge_readings([dict(total='162.00', firm_id=7, items=[],
                                     import_check={'expected_rows':[1, 2]})])
        layouts = {7: ('Örnek Gıda', 'net_vat')}
        result = apply_supplier_layout(draft, [table.text()], layouts)
        self.assertEqual(result['coverage']['errors'], [])
        self.assertEqual([(row['net'], row['vat']) for row in result['items']],
                         [('100.00', '8.00'), ('50.00', '4.00')])
        self.assertEqual([row['source_price'] for row in result['items']], ['10', '10'])
        self.assertIn('Örnek Gıda', result['warnings'][0])
        wrong_total = apply_supplier_layout(dict(draft, total='163.00'), [table.text()], layouts)
        self.assertEqual(wrong_total['items'], [])
        self.assertIn('doğrulanamadı', wrong_total['warnings'][-1])
        mismatch = apply_supplier_layout(draft, [table.text()], layouts, selected_firm=8)
        self.assertEqual(mismatch['items'], [])
        self.assertIn('uyuşmadı', mismatch['warnings'][-1])

    def test_unambiguous_package_glyphs_are_cleaned(self):
        self.assertEqual(
            _clean_product('PET FRK GAZOZ 2.51 6X 20KCAL FE'),
            'PET FRK GAZOZ 2.5L 6X 20KCAL FE',
        )
        self.assertEqual(_clean_product('PET PC KOLA 1.5L 12X 7OKCAL FE'), 'PET PC KOLA 1.5L 12X 70KCAL FE')
    def test_reconstructs_cells_from_separate_blocks_and_wrapped_name(self):
        data = TsvBuilder()
        data.item(1, "8223298", "BENIMO KOMBO KREMALI 96GX18 KL", 18,
                  "27,7200", "4,69", "470,13", 100, wrap=True)
        data.item(2, "2244700", "GONG BAHARATLI PEYNIRLI 34GX18 KL", 18,
                  "14,2600", "2,42", "241,85", 150)

        result = parse_tsv_table(data.text())

        self.assertEqual(result["expected_rows"], [1, 2])
        self.assertEqual(len(result["items"]), 2)
        first = result["items"][0]
        self.assertEqual(first["code"], "8223298")
        self.assertEqual(first["quantity"], "18")
        self.assertEqual(first["price"], "26.3789")
        self.assertEqual(first["line_total"], "470.13")
        self.assertEqual(first["vat"], "4.69")
        self.assertIn("96GX18", first["product"])

    def test_overlapping_page_fragments_are_deduplicated(self):
        first = TsvBuilder()
        first.item(1, "1111111", "URUN BIR 100GX12", 10, "10,0000", "1,00", "100,00", 100)
        first.item(2, "2222222", "URUN IKI 50GX24", 20, "20,0000", "4,00", "400,00", 150)
        second = TsvBuilder()
        second.item(2, "2222222", "URUN IKI 50GX24", 20, "20,0000", "4,00", "400,00", 100)
        second.item(3, "3333333", "URUN UC 30GX10", 5, "30,0000", "1,50", "150,00", 150)

        result = parse_tsv_table(first.text() + second.text())

        self.assertEqual(result["expected_rows"], [1, 2, 3])
        self.assertEqual([item["row_number"] for item in result["items"]], [1, 2, 3])
        self.assertFalse(any("çelişkili" in warning for warning in result["warnings"]))

    def test_summary_text_below_final_row_is_not_part_of_product_name(self):
        data = TsvBuilder()
        data.item(36, "4431800", "NERO 110GX18 KL", 18, "23,7600", "4,26", "426,15", 100)
        data.item(37, "4933600", "BUR. KREMALI 100GX12 DP", 12, "19,8000", "2,37", "236,75", 150)
        # Summary text begins below the final row's half-row boundary.
        data.add("ETIGENISK", 185, 180, 75)

        result = parse_tsv_table(data.text())

        self.assertEqual(result["items"][-1]["product"], "BUR. KREMALI 100GX12 DP")

    def test_missing_detected_row_is_reported(self):
        data = TsvBuilder()
        data.item(1, "1111111", "URUN BIR", 10, "10,0000", "1,00", "100,00", 100)
        data.item(3, "3333333", "URUN UC", 5, "30,0000", "1,50", "150,00", 150)

        result = parse_tsv_table(data.text())

        self.assertEqual(result["expected_rows"], [1, 2, 3])
        self.assertEqual([item["row_number"] for item in result["items"]], [1, 3])
        self.assertTrue(any("Eksik fatura kalemleri: 2" in warning for warning in result["warnings"]))

    def test_suspicious_product_code_is_not_silently_corrected(self):
        data = TsvBuilder()
        data.item(1, "12O4567", "URUN BIR", 10, "10,0000", "1,00", "100,00", 100)

        result = parse_tsv_table(data.text())

        self.assertEqual(result["items"][0]["code"], "12O4567")
        self.assertIn("otomatik olarak rakama çevrilmedi", result["items"][0]["review"])

    def test_grid_punctuation_and_visual_unit_order_do_not_drop_row(self):
        data = TsvBuilder()
        data.item(12, "2744900", "CRAX CHILLI LIME 50GX20", 20,
                  "19,8000", "3,73", "373,13", 100,
                  ordinal_text="12.", unit_above_quantity=True)

        result = parse_tsv_table(data.text())

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["row_number"], 12)
        self.assertEqual(result["items"][0]["quantity"], "20")

    def test_different_supplier_column_widths_use_geometric_fallback(self):
        data = TsvBuilder()
        data.add("1", 30, 100, 16)
        data.add("9876543", 75, 100, 65)
        data.add("FARKLI", 165, 100, 50)
        data.add("MARKA", 220, 100, 45)
        data.add("URUN", 270, 100, 40)
        data.add("12", 355, 100, 20)
        data.add("Adet", 382, 100, 36)
        data.add("25,0000", 470, 100, 62)
        data.add("%10,00", 575, 100, 50)
        data.add("25,00", 665, 100, 48)
        data.add("300,00", 755, 100, 55)

        result = parse_tsv_table(data.text())

        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["row_number"], 1)
        self.assertEqual(item["product"], "FARKLI MARKA URUN")
        self.assertEqual(item["quantity"], "12")
        self.assertEqual(item["line_total"], "300.00")
        self.assertEqual(item["vat"], "25.00")
        self.assertIn("Farklı fatura sütun düzeniyle", item["review"])

    def test_low_confidence_numbers_require_user_review(self):
        data = TsvBuilder()
        data.item(1, "8223298", "BENIMO KOMBO 96GX18", 18,
                  "27,7200", "4,69", "470,13", 100, confidence=40)

        result = parse_tsv_table(data.text())

        self.assertIn("düşük güvenle", result["items"][0]["review"])

    def test_conflicting_overlap_is_not_silently_selected(self):
        first = TsvBuilder()
        first.item(1, "1111111", "URUN BIR", 10, "10,0000", "1,00", "100,00", 100)
        second = TsvBuilder()
        second.item(1, "1111111", "URUN BIR", 100, "10,0000", "1,00", "100,00", 100)

        result = parse_tsv_table(first.text() + second.text())

        self.assertEqual(result["items"], [])
        self.assertTrue(any("çelişkili" in warning for warning in result["warnings"]))
        self.assertTrue(any("Eksik fatura kalemleri: 1" in warning for warning in result["warnings"]))

    def test_stray_large_number_does_not_inflate_expected_item_count(self):
        data = TsvBuilder()
        data.item(1, "1111111", "URUN BIR", 10, "10,0000", "1,00", "100,00", 100)
        data.item(2, "2222222", "URUN IKI", 5, "20,0000", "1,00", "100,00", 150)
        data.add("85", 50, 500, 20)
        data.add("9999999", 86, 500, 65)

        result = parse_tsv_table(data.text())

        self.assertEqual(result["expected_rows"], [1, 2])
        self.assertTrue(any("85" in warning and "göz ardı" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
