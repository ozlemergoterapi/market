"""Reconstruct invoice item rows from Tesseract TSV word coordinates.

The plain-text output produced by Tesseract often reads a ruled invoice table
column by column.  This module deliberately ignores Tesseract's block and line
ordering and rebuilds rows from the physical word positions instead.

It currently contains a conservative fallback profile for the Koseoglu invoice
layout used by the application.  Values that cannot be reconciled are omitted
and reported for review; OCR characters are never silently changed.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


TSV_FIELDS = (
    "level", "page_num", "block_num", "par_num", "line_num", "word_num",
    "left", "top", "width", "height", "conf", "text",
)


@dataclass(frozen=True)
class Word:
    document: int
    page: int
    left: int
    top: int
    width: int
    height: int
    confidence: float
    text: str

    @property
    def right(self):
        return self.left + self.width

    @property
    def center_x(self):
        return self.left + self.width / 2

    @property
    def center_y(self):
        return self.top + self.height / 2


@dataclass(frozen=True)
class Anchor:
    number: int
    code: str
    ordinal: Word
    code_word: Word

    @property
    def center_y(self):
        return (self.ordinal.center_y + self.code_word.center_y) / 2


def _plain(value):
    value = value.replace("ı", "i").replace("İ", "I")
    return "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    ).lower()


def _read_words(tsv_text):
    """Read one or more concatenated Tesseract TSV documents."""
    words = []
    document = -1
    header_seen = False
    for raw in io.StringIO(tsv_text):
        raw = raw.rstrip("\r\n")
        if not raw:
            continue
        columns = next(csv.reader([raw], delimiter="\t"))
        if columns and columns[0].strip().lower() == "level":
            document += 1
            header_seen = True
            continue
        if not header_seen or len(columns) < len(TSV_FIELDS):
            continue
        # A tab in OCR text is legal but uncommon. Preserve it as part of text.
        if len(columns) > len(TSV_FIELDS):
            columns = columns[:11] + [" ".join(columns[11:])]
        row = dict(zip(TSV_FIELDS, columns))
        try:
            if int(row["level"]) != 5 or not row["text"].strip():
                continue
            words.append(Word(
                document=max(document, 0),
                page=max(int(row["page_num"]), 1),
                left=max(int(row["left"]), 0),
                top=max(int(row["top"]), 0),
                width=max(int(row["width"]), 1),
                height=max(int(row["height"]), 1),
                confidence=float(row["conf"]),
                text=row["text"].strip(),
            ))
        except (TypeError, ValueError):
            # Malformed diagnostic/output lines must not become invoice data.
            continue
    return words


def _token(value):
    return re.sub(r"[^A-Za-z0-9]", "", value)


def _anchors(words):
    """Find row-number/product-code pairs without trusting TSV line ids."""
    if not words:
        return []
    page_width = max(word.right for word in words)
    candidates = []
    for ordinal in words:
        clean_number = re.sub(r"[^0-9]", "", ordinal.text)
        # Ruled cells are often returned as ``12.``, ``21 |`` or ``= 22``.
        # Accept punctuation around an otherwise numeric ordinal, never letters.
        if not clean_number or re.search(r"[A-Za-z]", ordinal.text):
            continue
        number = int(clean_number)
        if not 1 <= number <= 500 or ordinal.center_x > page_width * .20:
            continue
        nearby = []
        for possible_code in words:
            code = _token(possible_code.text)
            if not 5 <= len(code) <= 14 or not any(char.isdigit() for char in code):
                continue
            if possible_code.left <= ordinal.right:
                continue
            if possible_code.center_x > page_width * .34:
                continue
            vertical = abs(possible_code.center_y - ordinal.center_y)
            if vertical > max(ordinal.height, possible_code.height) * 1.35:
                continue
            horizontal = possible_code.left - ordinal.right
            if horizontal > page_width * .18:
                continue
            nearby.append((horizontal + vertical * 2, possible_code, code))
        if nearby:
            _, code_word, code = min(nearby, key=lambda value: value[0])
            candidates.append(Anchor(number, code, ordinal, code_word))

    # Tesseract occasionally emits the same word twice in overlapping blocks.
    result = []
    for anchor in sorted(candidates, key=lambda a: (a.center_y, a.number, a.code)):
        if any(
            old.number == anchor.number and old.code == anchor.code
            and abs(old.center_y - anchor.center_y) <= max(old.ordinal.height, anchor.ordinal.height)
            for old in result
        ):
            continue
        result.append(anchor)
    return result


def _decimal(text):
    compact = re.sub(r"[^0-9,.-]", "", text)
    if not compact or compact in {"-", ".", ","}:
        raise InvalidOperation()
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif "," in compact:
        compact = compact.replace(".", "").replace(",", ".")
    elif compact.count(".") > 1:
        head, tail = compact.rsplit(".", 1)
        compact = head.replace(".", "") + "." + tail
    return Decimal(compact)


def _decimal_text(value):
    value = value.normalize()
    return format(value, "f")


def _column_text(words, left, right):
    selected = _column_words(words, left, right)
    return " ".join(word.text for word in sorted(selected, key=lambda w: (w.top, w.left)))


def _column_words(words, left, right):
    return [word for word in words if left <= word.center_x < right]


def _quantity(text):
    match = re.search(
        r"(?<![0-9])([0-9]+(?:[.,][0-9]+)?)\s*"
        # A final package token (for example ``KL``) can cross the visual
        # product/quantity boundary. Allow it without treating it as a number.
        r"(?:[A-Za-zÇĞİÖŞÜçğıöşü.]+\s+){0,2}"
        r"(Adet|Ad\.?|Kg|Koli|Lt|Litre|Paket|Kutu)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        reversed_match = re.search(
            r"(Adet|Ad\.?|Kg|Koli|Lt|Litre|Paket|Kutu)\s*"
            r"([0-9]+(?:[.,][0-9]+)?)\b",
            text,
            re.IGNORECASE,
        )
        if not reversed_match:
            raise ValueError()
        return _decimal(reversed_match.group(2)), reversed_match.group(1).rstrip(".").lower()
    return _decimal(match.group(1)), match.group(2).rstrip(".").lower()


def _amount(text):
    match = re.search(r"[0-9]+(?:[.,][0-9]+)+", text)
    if not match:
        # The zero-priced test item on a real invoice may still be an integer.
        match = re.search(r"(?<![0-9])[0-9]+(?![0-9])", text)
    if not match:
        raise InvalidOperation()
    return _decimal(match.group())


def _same_core(left, right):
    return (
        left["code"] == right["code"]
        and left["quantity"] == right["quantity"]
        and left["line_total"] == right["line_total"]
        and left["net"] == right["net"]
        and left["vat"] == right["vat"]
    )


def _item_from_band(anchor, words, table_left, table_right):
    """Extract one row using the Koseoglu table's stable column proportions."""
    table_width = max(table_right - table_left, 1)

    def boundary(ratio):
        return table_left + table_width * ratio

    # S.No | code | product | quantity | raw price | discounts | VAT% | VAT | net
    product_range = (boundary(.15), boundary(.405))
    quantity_range = (boundary(.405), boundary(.505))
    raw_range = (boundary(.505), boundary(.575))
    vat_range = (boundary(.82), boundary(.92))
    net_range = (boundary(.92), boundary(1.02))
    product_text = _column_text(words, *product_range)
    quantity_text = _column_text(words, *quantity_range)
    raw_text = _column_text(words, *raw_range)
    vat_text = _column_text(words, *vat_range)
    net_text = _column_text(words, *net_range)

    if not product_text.strip():
        raise ValueError("ürün adı")
    quantity, unit = _quantity(quantity_text)
    raw_price = _amount(raw_text)
    vat = _amount(vat_text)
    net = _amount(net_text)
    if quantity <= 0 or raw_price <= 0 or vat < 0 or net <= 0:
        raise ValueError("sayısal alan")

    gross = net + vat
    price = (gross / quantity).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    review = ""
    if any(char.isalpha() for char in anchor.code):
        review = (
            "Ürün kodunda harf okundu; kodu faturayla karşılaştırın. "
            "OCR karakterleri otomatik olarak rakama çevrilmedi."
        )
    # Product code and quantity drive later price matching. Monetary OCR is
    # independently guarded by per-row arithmetic and the invoice grand total.
    critical_words = [anchor.code_word] + [
        word for word in _column_words(words, *quantity_range)
        if any(char.isdigit() for char in word.text)
    ]
    critical_confidence = [word.confidence for word in critical_words if word.confidence >= 0]
    product_confidence = [
        word.confidence for word in _column_words(words, *product_range)
        if word.confidence >= 0
    ]
    if critical_confidence and (
        min(critical_confidence) < 45
        or statistics.fmean(critical_confidence) < 65
    ):
        warning = "Ürün kodu veya miktar düşük güvenle okundu; rakamları faturayla karşılaştırın."
        review = (review + " " + warning).strip()
    if product_confidence and statistics.fmean(product_confidence) < 55:
        warning = "Ürün adı düşük güvenle okundu; adı ve gramajı faturayla karşılaştırın."
        review = (review + " " + warning).strip()
    if net > quantity * raw_price + Decimal(".10") or net < quantity * raw_price * Decimal(".5"):
        warning = "Miktar ile satır/birim fiyatı arasında büyük fark var; satırı kontrol edin."
        review = (review + " " + warning).strip()

    confidence_values = [word.confidence for word in words if word.confidence >= 0]
    confidence = statistics.fmean(confidence_values) if confidence_values else -1
    item = {
        "row_number": anchor.number,
        "line_total": f"{net:.2f}",
        "review": review,
        "product": re.sub(r"\s+", " ", product_text).strip()[:500],
        "unit": unit,
        "price": f"{price:.2f}",
        "code": anchor.code,
        "quantity": _decimal_text(quantity),
        "source_price": _decimal_text(raw_price),
        "net": f"{net:.2f}",
        "vat": f"{vat:.2f}",
    }
    return item, confidence


def _item_from_geometry(anchor, words):
    """Extract a row without assuming one supplier's fixed column widths.

    The product code anchors the left side.  The first explicit quantity unit
    (Adet, Kg, Koli...) separates the product name from numeric columns.  The
    first monetary value after it is the source unit price; the final two are
    VAT and the net row amount, matching supported Turkish invoice tables.
    """
    ordered = sorted(
        [word for word in words if word.center_x > anchor.code_word.center_x],
        key=lambda word: (word.center_x, word.top),
    )
    unit_pattern = re.compile(r"(?:Adet|Ad\.?|Kg|Koli|Lt|Litre|Paket|Kutu)", re.IGNORECASE)
    quantity = unit = None
    quantity_left = quantity_right = None
    unit_index = None
    for index, word in enumerate(ordered):
        if not unit_pattern.search(word.text):
            continue
        windows = [
            (index, index + 1),
            (index - 1, index + 1),
            (index - 2, index + 1),
            (index, index + 2),
            (index, index + 3),
        ]
        for start, end in windows:
            start, end = max(0, start), min(len(ordered), end)
            sample = " ".join(entry.text for entry in ordered[start:end])
            try:
                candidate, candidate_unit = _quantity(sample)
            except (InvalidOperation, ValueError):
                continue
            number_words = [
                entry for entry in ordered[start:end]
                if re.search(r"[0-9]", entry.text)
                and abs(entry.center_y - word.center_y) <= max(entry.height, word.height) * 1.8
            ]
            if candidate > 0 and number_words:
                quantity, unit = candidate, candidate_unit
                quantity_left = min([word.left] + [entry.left for entry in number_words])
                quantity_right = max([word.right] + [entry.right for entry in number_words])
                unit_index = index
                break
        if quantity is not None:
            break
    if quantity is None:
        raise ValueError("miktar")

    product_words = [
        word for word in words
        if word.left >= anchor.code_word.right - max(anchor.code_word.width * .08, 3)
        and word.right <= quantity_left
        and word is not anchor.code_word
    ]
    product_text = " ".join(word.text for word in sorted(product_words, key=lambda word: (word.top, word.left)))
    if not product_text.strip():
        raise ValueError("ürün adı")

    amount_words = [word for word in ordered[unit_index + 1:] if word.left >= quantity_right - 2]
    amount_text = " ".join(word.text for word in amount_words)
    amount_text = re.sub(r"(?<=\d)([.,])\s+(?=\d)", r"\1", amount_text)
    monetary = []
    for match in re.finditer(r"(?<!\d)\d+(?:[.,]\d+)+(?!\d)", amount_text):
        try:
            monetary.append(_decimal(match.group()))
        except InvalidOperation:
            pass
    if len(monetary) < 3:
        raise ValueError("tutarlar")
    raw_price, vat, net = monetary[0], monetary[-2], monetary[-1]
    if raw_price <= 0 or vat < 0 or net <= 0:
        raise ValueError("sayısal alan")

    gross = net + vat
    price = (gross / quantity).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    review = "Farklı fatura sütun düzeniyle okundu; ürün, miktar ve tutarı faturayla karşılaştırın."
    if any(char.isalpha() for char in anchor.code):
        review += " Ürün kodunda harf okundu; kod otomatik değiştirilmedi."
    confidence_values = [word.confidence for word in words if word.confidence >= 0]
    confidence = statistics.fmean(confidence_values) if confidence_values else -1
    return {
        "row_number": anchor.number,
        "line_total": f"{net:.2f}",
        "review": review,
        "product": re.sub(r"\s+", " ", product_text).strip()[:500],
        "unit": unit,
        "price": f"{price:.2f}",
        "code": anchor.code,
        "quantity": _decimal_text(quantity),
        "source_price": _decimal_text(raw_price),
        "net": f"{net:.2f}",
        "vat": f"{vat:.2f}",
    }, confidence


def _page_items(words):
    anchors = _anchors(words)
    if not anchors:
        return [], [], []
    anchors.sort(key=lambda anchor: anchor.center_y)
    heights = [max(anchor.ordinal.height, anchor.code_word.height) for anchor in anchors]
    gaps = [
        right.center_y - left.center_y
        for left, right in zip(anchors, anchors[1:])
        if 0 < right.center_y - left.center_y <= max(statistics.median(heights) * 8, 120)
    ]
    typical_gap = statistics.median(gaps) if gaps else statistics.median(heights) * 3
    table_left = statistics.median(anchor.ordinal.left for anchor in anchors)
    code_gap = statistics.median(anchor.code_word.left - anchor.ordinal.left for anchor in anchors)

    first_y = anchors[0].center_y - typical_gap * .55
    # Stop the final row at the same half-row boundary used between rows.
    # Invoice discount/summary labels can start immediately below the table;
    # extending almost a full row used to append those labels to the last
    # product name (for example ``ETIGENISK`` on a digital PDF).
    last_y = anchors[-1].center_y + typical_gap * .55
    row_area = [word for word in words if first_y <= word.center_y < last_y]
    observed_right = max((word.right for word in row_area), default=table_left + 1)
    # In this layout the code column begins about 4% into the table. This
    # estimate keeps the final amount column stable even when its text was lost.
    estimated_right = table_left + max(code_gap / .04, 1)
    table_right = max(observed_right, estimated_right)

    parsed = []
    warnings = []
    for index, anchor in enumerate(anchors):
        top = (
            (anchors[index - 1].center_y + anchor.center_y) / 2
            if index else anchor.center_y - typical_gap * .55
        )
        bottom = (
            (anchor.center_y + anchors[index + 1].center_y) / 2
            if index + 1 < len(anchors) else anchor.center_y + typical_gap * .55
        )
        band = [word for word in words if top <= word.center_y < bottom]
        try:
            item, confidence = _item_from_band(anchor, band, table_left, table_right)
            parsed.append((item, confidence))
        except (InvalidOperation, ValueError):
            try:
                item, confidence = _item_from_geometry(anchor, band)
                parsed.append((item, confidence))
            except (InvalidOperation, ValueError):
                warnings.append(
                    f"{anchor.number}. ürün satırının hücreleri eksiksiz okunamadı; fotoğrafla karşılaştırın."
                )
    return parsed, [anchor.number for anchor in anchors], warnings


def parse_tsv_table(tsv_text):
    """Return parse_invoice-compatible items reconstructed from TSV words.

    The return value contains ``items``, ``expected_rows`` and ``warnings``.
    Rows repeated in overlapping screenshots are de-duplicated only when their
    code, quantity and amounts agree. Conflicting repeats are left out.
    """
    words = _read_words(tsv_text)
    pages = {}
    for word in words:
        pages.setdefault((word.document, word.page), []).append(word)

    candidates = {}
    detected = set()
    warnings = []
    for page_words in pages.values():
        rows, numbers, page_warnings = _page_items(page_words)
        detected.update(numbers)
        warnings.extend(page_warnings)
        for item, confidence in rows:
            candidates.setdefault(item["row_number"], []).append((item, confidence))

    items = []
    for number in sorted(candidates):
        choices = candidates[number]
        first = choices[0][0]
        if any(not _same_core(first, choice[0]) for choice in choices[1:]):
            warnings.append(
                f"{number}. satır farklı fotoğraflarda çelişkili okundu; otomatik doldurulmadı."
            )
            continue
        best = max(choices, key=lambda choice: (choice[1], len(choice[0]["product"])))
        item = dict(best[0])
        products = {_plain(choice[0]["product"]) for choice in choices}
        if len(products) > 1:
            extra = "Tekrarlanan fotoğraflarda ürün adı farklı okundu; seçilen adı kontrol edin."
            item["review"] = (item["review"] + " " + extra).strip()
        items.append(item)

    expected_rows = list(range(1, max(detected) + 1)) if detected else []
    missing = sorted(set(expected_rows) - {item["row_number"] for item in items})
    if missing:
        warnings.append(
            "Eksik fatura kalemleri: " + ", ".join(map(str, missing))
            + ". Eksik kalemlerle kaydedilmemeli."
        )
    if not detected:
        warnings.append("TSV içinde ürün sıra numarası ve ürün kodu birlikte bulunamadı.")
    return {
        "items": items,
        "expected_rows": expected_rows,
        "detected_rows": sorted(detected),
        "warnings": list(dict.fromkeys(warnings)),
    }

