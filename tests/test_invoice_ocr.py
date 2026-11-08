import unittest

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
        self.assertEqual(first["price"], "26.38")
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


if __name__ == "__main__":
    unittest.main()
