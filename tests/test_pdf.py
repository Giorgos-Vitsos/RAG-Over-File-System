import io

import pymupdf
import pytest
from PIL import Image

from rag_fs.config import OCRConfig
from rag_fs.ingest.parsers import OCR_PARSERS, PARSERS
from rag_fs.ingest.parsers import ocr as ocr_module
from rag_fs.ingest.parsers.pdf import read_pdf
from test_ocr import SENTENCE, picture
from test_parsers import parse_one

PORTRAIT = (595, 842)
LANDSCAPE = (842, 595)


# the text of a pdf, without the flag that says if OCR was used
def pdf_text(path):
    return read_pdf(path)[0]


# a pdf with one page for each html string, as bytes
def pdf_bytes(pages, size=PORTRAIT):
    doc = pymupdf.open()
    for html in pages:
        page = doc.new_page(width=size[0], height=size[1])
        if html:
            page.insert_htmlbox(pymupdf.Rect(50, 50, 300, size[1] - 50), html)
    return doc.tobytes()


# writes the pages to a pdf file and reads it
def read(tmp_path, pages, size=PORTRAIT):
    f = tmp_path / "doc.pdf"
    f.write_bytes(pdf_bytes(pages, size))
    return pdf_text(f)


# (html of one page, text)
PAGE_CASES = {
    "paragraph that wraps becomes one line": (
        "<p>Πρώτη παράγραφος με αρκετές λέξεις ώστε να αλλάζει γραμμή μέσα στο κουτί της σελίδας.</p>",
        "Πρώτη παράγραφος με αρκετές λέξεις ώστε να αλλάζει γραμμή μέσα στο κουτί της σελίδας."),
    "paragraphs are blocks": ("<p>Πρώτη.</p><p>Δεύτερη.</p>", "Πρώτη.\n\nΔεύτερη."),
    "word broken at the line end is joined": ("<p>Η πληρο-<br>φορία.</p>", "Η πληροφορία."),
    "real hyphen used elsewhere is kept": (
        "<p>Ένα well-<br>known θέμα, πολύ well-known.</p>", "Ένα well-known θέμα, πολύ well-known."),
    "joined form used elsewhere wins": ("<p>Το e-<br>mail και το email.</p>", "Το email και το email."),
    "both forms used elsewhere, joined wins": ("<p>Το e-<br>mail, το e-mail και το email.</p>", "Το email, το e-mail και το email."),
    "pieces of broken words are not words": (
        "<p>Ένα well-<br>known και wellknown-<br>ish, πολύ well-known.</p>",
        "Ένα well-known και wellknownish, πολύ well-known."),
    "capitals do not matter when looking elsewhere": (
        "<p>Well-known αρχή. Ένα well-<br>known και Well-<br>known.</p>",
        "Well-known αρχή. Ένα well-known και Well-known."),
    "unknown word is joined": ("<p>Να co-<br>operate.</p>", "Να cooperate."),
    "capital after the hyphen keeps it": ("<p>Το μάθημα ΗΥ-<br>Μεταφραστές.</p>", "Το μάθημα ΗΥ-Μεταφραστές."),
    "number after the hyphen keeps it": ("<p>Το ΗΥ-<br>340.</p>", "Το ΗΥ-340."),
    "minus sign with a space is not a broken word": ("<p>Τιμή -<br>5 βαθμοί.</p>", "Τιμή - 5 βαθμοί."),
    "ligature is split into letters": ("<p>Ένα \ufb01le και \ufb02ow.</p>", "Ένα file και flow."),
    "block of only spaces leaves no gap": ("<p>Α</p><p>&nbsp;</p><p>Β</p>", "Α\n\nΒ"),
    "extra spaces are collapsed": ("<p>α&nbsp;&nbsp;&nbsp;β</p>", "α β"),
}


# text of a single page
@pytest.mark.parametrize("html, expected", PAGE_CASES.values(), ids=PAGE_CASES.keys())
def test_read_pdf_page(tmp_path, html, expected):
    assert read(tmp_path, [html]) == expected


# (pages, page size, text)
DOC_CASES = {
    "pages of a document are joined without a mark": (["<p>Α</p>", "<p>Β</p>"], PORTRAIT, "Α\n\nΒ"),
    "empty page in a document leaves no gap": (["<p>Α</p>", "", "<p>Β</p>"], PORTRAIT, "Α\n\nΒ"),
    "landscape pages are slides": (["<p>Α</p>", "<p>Β</p>"], LANDSCAPE, "# Slide 1\n\nΑ\n\n# Slide 2\n\nΒ"),
    "empty slide keeps its number": (["<p>Α</p>", "", "<p>Γ</p>"], LANDSCAPE,
                                     "# Slide 1\n\nΑ\n\n# Slide 2\n\n# Slide 3\n\nΓ"),
    "no text at all": (["", ""], PORTRAIT, ""),
}


# how the pages of a pdf are put together
@pytest.mark.parametrize("pages, size, expected", DOC_CASES.values(), ids=DOC_CASES.keys())
def test_read_pdf_pages(tmp_path, pages, size, expected):
    assert read(tmp_path, pages, size) == expected


# slides only when most pages are landscape: one landscape page out of two is a document
def test_half_landscape_is_a_document(tmp_path):
    doc = pymupdf.open()
    for (w, h), text in [(LANDSCAPE, "Α"), (PORTRAIT, "Β")]:
        doc.new_page(width=w, height=h).insert_htmlbox(pymupdf.Rect(50, 50, 300, 400), f"<p>{text}</p>")
    f = tmp_path / "mixed.pdf"
    doc.save(f)
    assert pdf_text(f) == "Α\n\nΒ"


# through parse_file: text, scanned (no text), password, broken file
@pytest.mark.parametrize("name, data, status, text", [
    ("notes.pdf", pdf_bytes(["<p>Σημειώσεις.</p>"]), "ok", "Σημειώσεις."),
    ("SCAN.PDF", pdf_bytes(["", ""]), "empty", ""),
    ("broken.pdf", b"%PDF-1.7 this is not a real pdf", "failed", ""),
], ids=["text", "no text", "broken"])
def test_parse_file_reads_pdf(tmp_path, name, data, status, text):
    doc = parse_one(tmp_path, name, data)
    assert (doc.parse_status, doc.content_type, doc.raw_text) == (status, "prose", text)


# a pdf with a password can not be read
def test_parse_file_pdf_with_password_fails(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 300, 400), "<p>Μυστικό.</p>")
    data = doc.tobytes(encryption=getattr(pymupdf, "PDF_ENCRYPT_AES_256"), user_pw="pw", owner_pw="pw")
    assert parse_one(tmp_path, "secret.pdf", data).parse_status == "failed"




# a pdf whose pages have a body in the middle and, if given, a header at the top and a footer at the bottom
# ({n} becomes the page number); None leaves that part out on that page
def edges_pdf(tmp_path, bodies, headers=None, footers=None, size=PORTRAIT):
    w, h = size
    doc = pymupdf.open()
    for i, body in enumerate(bodies):
        page = doc.new_page(width=w, height=h)
        page.insert_htmlbox(pymupdf.Rect(50, 0.3 * h, w - 50, 0.7 * h), body)
        for texts, top in [(headers, 0.03 * h), (footers, 0.93 * h)]:
            if texts and texts[i] is not None:
                page.insert_htmlbox(pymupdf.Rect(50, top, w - 50, top + 0.05 * h), texts[i].format(n=i + 1))
    f = tmp_path / "edges.pdf"
    doc.save(f)
    return pdf_text(f)



THREE = ["<p>Α</p>", "<p>Β</p>", "<p>Γ</p>"]

# (bodies, headers, footers, text)
EDGE_CASES = {
    "page x of y footer": (THREE, None, ["<p>Σελίδα {n} από 3</p>"] * 3, "Α\n\nΒ\n\nΓ"),
    "page number alone": (THREE, None, ["<p>{n}</p>"] * 3, "Α\n\nΒ\n\nΓ"),
    "header on every page comes back once": (
        THREE, ["<p>ΗΥ-340 Μεταφραστές</p>"] * 3, None, "ΗΥ-340 Μεταφραστές\n\nΑ\n\nΒ\n\nΓ"),
    "the header comes back, the changing page number does not": (
        THREE, ["<p>ΗΥ-340</p>"] * 3, ["<p>- {n} -</p>"] * 3, "ΗΥ-340\n\nΑ\n\nΒ\n\nΓ"),
    "spaces and capitals do not matter": (
        THREE, ["<p>ΗΥ-340 Μεταφραστές</p>", "<p>ΗΥ-340&nbsp; Μεταφραστές</p>", "<p>ηυ-340 μεταφραστές</p>"], None,
        "ΗΥ-340 Μεταφραστές\n\nΑ\n\nΒ\n\nΓ"),
    "footer on half of the pages": (
        THREE + ["<p>Δ</p>"], None, ["<p>Πρόχειρο</p>", None, "<p>Πρόχειρο</p>", None], "Πρόχειρο\n\nΑ\n\nΒ\n\nΓ\n\nΔ"),
    "page with only a footer becomes empty": (
        ["<p>Α</p>", "", "<p>Γ</p>"], None, ["<p>{n}</p>"] * 3, "Α\n\nΓ"),
    "header text in the body is kept": (
        ["<p>Το ΗΥ-340 είναι μάθημα.</p>", "<p>ΗΥ-340</p>", "<p>Γ</p>"], ["<p>ΗΥ-340</p>"] * 3, None,
        "ΗΥ-340\n\nΤο ΗΥ-340 είναι μάθημα.\n\nΗΥ-340\n\nΓ"),
}


# repeated text at the top or bottom of the pages is removed
@pytest.mark.parametrize("bodies, headers, footers, expected", EDGE_CASES.values(), ids=EDGE_CASES.keys())
def test_headers_and_footers_are_removed(tmp_path, bodies, headers, footers, expected):
    assert edges_pdf(tmp_path, bodies, headers, footers) == expected


# (bodies, headers, footers, text); pymupdf gives the blocks in the order they were drawn: body, header, footer
KEPT_CASES = {
    "two pages are too few": (["<p>Α</p>", "<p>Β</p>"], None, ["<p>{n}</p>"] * 2, "Α\n\n1\n\nΒ\n\n2"),
    "on one page out of four": (
        THREE + ["<p>Δ</p>"], None, ["<p>Πρόχειρο</p>", None, None, None], "Α\n\nΠρόχειρο\n\nΒ\n\nΓ\n\nΔ"),
    "different title on every page": (
        THREE, ["<p>Εισαγωγή</p>", "<p>Μέθοδος</p>", "<p>Αποτελέσματα</p>"], None,
        "Α\n\nΕισαγωγή\n\nΒ\n\nΜέθοδος\n\nΓ\n\nΑποτελέσματα"),
    "the same text in the middle of the pages": (["<p>Άσκηση</p>"] * 3, None, None, "Άσκηση\n\nΆσκηση\n\nΆσκηση"),
}


# text at the edges that is not repeated on most pages, or repeated only in the body, stays
@pytest.mark.parametrize("bodies, headers, footers, expected", KEPT_CASES.values(), ids=KEPT_CASES.keys())
def test_text_that_is_not_a_header_stays(tmp_path, bodies, headers, footers, expected):
    assert edges_pdf(tmp_path, bodies, headers, footers) == expected


# slides lose their repeated footer too, and keep their numbers
def test_slides_lose_their_footer(tmp_path):
    text = edges_pdf(tmp_path, ["<p>Α</p>", "", "<p>Γ</p>"], None, ["<p>ΗΥ-358 Γραφικά {n}</p>"] * 3, LANDSCAPE)
    assert text == "# Slide 1\n\nΑ\n\n# Slide 2\n\n# Slide 3\n\nΓ"


# a pdf of 3 portrait pages, each with the same blocks: (top, bottom) as parts of the page height, and html
def placed_pdf(tmp_path, blocks):
    w, h = PORTRAIT
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=w, height=h)
        for top, bottom, html in blocks:
            page.insert_htmlbox(pymupdf.Rect(50, top * h, w - 50, bottom * h), html)
    f = tmp_path / "placed.pdf"
    doc.save(f)
    return pdf_text(f)


# only blocks that are whole inside the top or bottom 12% can be headers
@pytest.mark.parametrize("top, bottom, html, kept", [
    (0.03, 0.40, "<p>" + "Οδηγίες για όλες τις ασκήσεις του μαθήματος. " * 12 + "</p>", True),  # starts at the top, reaches the middle
    (0.20, 0.25, "<p>Άσκηση</p>", True),                                                    # repeated, but below the top 12%
    (0.03, 0.08, "<p>Άσκηση</p>", False),                                                   # repeated inside the top 12%
])
def test_only_blocks_inside_the_edges_are_headers(tmp_path, top, bottom, html, kept):
    text = placed_pdf(tmp_path, [(top, bottom, html), (0.5, 0.6, "<p>Σώμα.</p>")])
    assert (text.count("Σώμα.") == 3) and ((text.count("\n\n") == 5) == kept)


CELL_W, CELL_H = 120, 22


# draws a table with lines around every cell; cells are (row, col, rowspan, colspan, html)
def draw_table(page, y, cells):
    for r, c, rs, cs, html in cells:
        rect = pymupdf.Rect(50 + c * CELL_W, y + r * CELL_H, 50 + (c + cs) * CELL_W, y + (r + rs) * CELL_H)
        page.draw_rect(rect, color=(0, 0, 0), width=0.7)
        if html:
            page.insert_htmlbox(rect + (3, 3, -3, -3), f"<p>{html}</p>")


# a simple table from rows of html, one cell per value
def grid(rows):
    return [(r, c, 1, 1, v) for r, row in enumerate(rows) for c, v in enumerate(row)]


# a pdf whose pages hold text (y, html) and tables (y, cells), and its text
def tables_pdf(tmp_path, pages, size=PORTRAIT):
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=size[0], height=size[1])
        for y, thing in items:
            if isinstance(thing, str):
                page.insert_htmlbox(pymupdf.Rect(50, y, 500, y + 40), thing)
            else:
                draw_table(page, y, thing)
    f = tmp_path / "tables.pdf"
    doc.save(f)
    return pdf_text(f)


GRADES = grid([["Όνομα", "Βαθμός"], ["Μαρία", "9"], ["Νίκος", "7"]])
GRADES_TEXT = "Όνομα: Μαρία; Βαθμός: 9\nΌνομα: Νίκος; Βαθμός: 7"

# (pages, text)
TABLE_CASES = {
    "table between text": (
        [[(150, "<p>Πριν.</p>"), (250, GRADES), (400, "<p>Μετά.</p>")]],
        "Πριν.\n\n" + GRADES_TEXT + "\n\nΜετά."),
    "cell over two columns": (
        [[(250, grid([["Όνομα", "Πρόοδος", "Τελικό"]]) + [(1, 0, 1, 1, "Μαρία"), (1, 1, 1, 2, "9")]
          + [(2, 0, 1, 1, "Νίκος"), (2, 1, 1, 1, "7"), (2, 2, 1, 1, "8")])]],
        "Όνομα: Μαρία; Πρόοδος: 9; Τελικό: 9\nΌνομα: Νίκος; Πρόοδος: 7; Τελικό: 8"),
    "cell over two rows": (
        [[(250, grid([["Μάθημα", "Φοιτητής"]]) + [(1, 0, 2, 1, "ΗΥ-340"), (1, 1, 1, 1, "Μαρία"), (2, 1, 1, 1, "Νίκος")])]],
        "Μάθημα: ΗΥ-340; Φοιτητής: Μαρία\nΜάθημα: ΗΥ-340; Φοιτητής: Νίκος"),
    "header of two rows with merged cells": (
        [[(250, [(0, 0, 2, 1, "Όνομα"), (0, 1, 1, 2, "Βαθμοί"), (1, 1, 1, 1, "Πρόοδος"), (1, 2, 1, 1, "Τελικό"),
                 (2, 0, 1, 1, "Μαρία"), (2, 1, 1, 1, "9"), (2, 2, 1, 1, "8"),
                 (3, 0, 1, 1, "Νίκος"), (3, 1, 1, 1, "7"), (3, 2, 1, 1, "6")])]],
        "Όνομα | Βαθμοί | Βαθμοί\nΌνομα: Μαρία; Πρόοδος: 9; Τελικό: 8\nΌνομα: Νίκος; Πρόοδος: 7; Τελικό: 6"),
    "broken word in a cell is joined": (
        [[(250, grid([["Όνομα", "Θέμα"], ["Μαρία", "πληρο-<br>φορία"], ["Νίκος", "δίκτυα"]]))]],
        "Όνομα: Μαρία; Θέμα: πληροφορία\nΌνομα: Νίκος; Θέμα: δίκτυα"),
    "table goes on in the next page": (
        [[(150, "<p>Πριν.</p>"), (720, grid([["Όνομα", "Βαθμός"], ["Μαρία", "9"]]))],
         [(40, grid([["Νίκος", "7"]])), (200, "<p>Μετά.</p>")]],
        "Πριν.\n\n" + GRADES_TEXT + "\n\nΜετά."),
    "header repeated on the next page is dropped": (
        [[(720, grid([["Όνομα", "Βαθμός"], ["Μαρία", "9"]]))],
         [(40, grid([["Όνομα", "Βαθμός"], ["Νίκος", "7"]]))]],
        GRADES_TEXT),
    "next page table with other columns is a new table": (
        [[(720, GRADES)], [(40, grid([["Μάθημα", "Αίθουσα", "Ώρα"], ["ΗΥ-340", "Α1", "9"], ["ΗΥ-252", "Β2", "11"]]))]],
        GRADES_TEXT + "\n\nΜάθημα: ΗΥ-340; Αίθουσα: Α1; Ώρα: 9\nΜάθημα: ΗΥ-252; Αίθουσα: Β2; Ώρα: 11"),
    "table in the middle of a page does not go on": (
        [[(300, GRADES)], [(40, grid([["Όνομα", "Βαθμός"], ["Ελένη", "8"]]))]],
        GRADES_TEXT + "\n\nΌνομα: Ελένη; Βαθμός: 8"),
}


FIRST = grid([["Όνομα", "Βαθμός"], ["Μαρία", "9"]])
NEXT = grid([["Όνομα", "Βαθμός"], ["Νίκος", "7"]])
TWO_TABLES = "Όνομα: Μαρία; Βαθμός: 9\n\nΌνομα: Νίκος; Βαθμός: 7"

# when a table does NOT go on in the next page: (pages, text)
NOT_GOING_ON = {
    "next table starts in the middle of the page": (
        [[(720, FIRST)], [(150, "<p>Νέα ενότητα.</p>"), (300, NEXT)]],
        "Όνομα: Μαρία; Βαθμός: 9\n\nΝέα ενότητα.\n\nΌνομα: Νίκος; Βαθμός: 7"),
    "table ends at two thirds of the page": ([[(520, FIRST)], [(40, NEXT)]], TWO_TABLES),
    "only the next page, not after a page with no table": (
        [[(720, FIRST)], [(150, "<p>Κείμενο.</p>")], [(40, NEXT)]],
        "Όνομα: Μαρία; Βαθμός: 9\n\nΚείμενο.\n\nΌνομα: Νίκος; Βαθμός: 7"),
    "only the first table of a page can go on": (
        [[(720, FIRST)], [(20, grid([["Μάθημα", "Αίθουσα", "Ώρα"], ["ΗΥ-340", "Α1", "9"]])), (80, NEXT)]],
        "Όνομα: Μαρία; Βαθμός: 9\n\nΜάθημα: ΗΥ-340; Αίθουσα: Α1; Ώρα: 9\n\nΌνομα: Νίκος; Βαθμός: 7"),
    "a piece that ends high stops the table": (
        [[(720, FIRST)], [(40, grid([["Νίκος", "7"]]))], [(40, grid([["Όνομα", "Βαθμός"], ["Ελένη", "8"]]))]],
        GRADES_TEXT + "\n\nΌνομα: Ελένη; Βαθμός: 8"),
}


# a table goes on only from the bottom of a page to the top of the very next one
@pytest.mark.parametrize("pages, expected", NOT_GOING_ON.values(), ids=NOT_GOING_ON.keys())
def test_tables_that_do_not_go_on(tmp_path, pages, expected):
    assert tables_pdf(tmp_path, pages) == expected


# tables at the top of every page are not headers, even when an empty block repeats at the bottom
def test_tables_at_the_top_are_not_headers(tmp_path):
    pages = [[(20, grid([["Όνομα", "Βαθμός"], [name, mark]])), (800, "<p>&nbsp;</p>")]
             for name, mark in [("Μαρία", "9"), ("Νίκος", "7"), ("Ελένη", "8")]]
    assert tables_pdf(tmp_path, pages) == (
        "Όνομα: Μαρία; Βαθμός: 9\n\nΌνομα: Νίκος; Βαθμός: 7\n\nΌνομα: Ελένη; Βαθμός: 8")


# a word written whole in a table decides a hyphen at a line end of the text
def test_words_in_tables_count_for_broken_words(tmp_path):
    text = tables_pdf(tmp_path, [[(150, "<p>Ένα well-<br>known θέμα.</p>"),
                                  (300, grid([["Όρος", "Είδος"], ["well-known", "επίθετο"], ["θέμα", "ουσιαστικό"]]))]])
    assert text == "Ένα well-known θέμα.\n\nΌρος: well-known; Είδος: επίθετο\nΌρος: θέμα; Είδος: ουσιαστικό"


# tables become rows of "column: value", like docx and xlsx
@pytest.mark.parametrize("pages, expected", TABLE_CASES.values(), ids=TABLE_CASES.keys())
def test_read_pdf_tables(tmp_path, pages, expected):
    assert tables_pdf(tmp_path, pages) == expected


# lines that are not a real table stay plain text
@pytest.mark.parametrize("cells, expected", [
    (grid([["Σημείωση", "Προσοχή"]]), "Σημείωση Προσοχή"),                     # a box of one row
    (grid([["Α", "", ""], ["", "", ""], ["", "", "Β"]]), "Α\n\nΒ"),            # mostly empty
    (grid([["Α"], ["Β"], ["Γ"]]), "Α\n\nΒ\n\nΓ"),                            # one column
], ids=["one row box", "mostly empty", "one column"])
def test_lines_that_are_not_a_table_stay_text(tmp_path, cells, expected):
    assert tables_pdf(tmp_path, [[(250, cells)]]) == expected


# a table on a slide
def test_table_on_a_slide(tmp_path):
    text = tables_pdf(tmp_path, [[(100, "<p>Βαθμοί</p>"), (200, GRADES)]], LANDSCAPE)
    assert text == "# Slide 1\n\nΒαθμοί\n\n" + GRADES_TEXT


# a pdf with one page for each html string and bookmarks [level, title, page]
def toc_pdf(tmp_path, pages, toc, size=PORTRAIT):
    doc = pymupdf.open()
    for html in pages:
        page = doc.new_page(width=size[0], height=size[1])
        if html:
            page.insert_htmlbox(pymupdf.Rect(50, 200, 400, size[1] - 150), html)
    doc.set_toc(toc)
    f = tmp_path / "toc.pdf"
    doc.save(f)
    return pdf_text(f)


# (pages, bookmarks, text)
TOC_CASES = {
    "title is a block": (["<p>Εισαγωγή</p><p>Κείμενο.</p>"], [[1, "Εισαγωγή", 1]], "# Εισαγωγή\n\nΚείμενο."),
    "levels": (["<p>Α</p><p>Β</p><p>Γ</p>"], [[1, "Α", 1], [2, "Β", 1], [3, "Γ", 1]], "# Α\n\n## Β\n\n### Γ"),
    "title starts a block": (
        ["<p>Εισαγωγή<br>Κείμενο της ενότητας.</p>"], [[1, "Εισαγωγή", 1]], "# Εισαγωγή\n\nΚείμενο της ενότητας."),
    "spaces and capitals do not matter": (
        ["<p>ΕΙΣΑΓΩΓΗ ΣΤΟ ΘΕΜΑ</p><p>Κείμενο.</p>"], [[1, "Εισαγωγη  στο θεμα", 1]], "# ΕΙΣΑΓΩΓΗ ΣΤΟ ΘΕΜΑ\n\nΚείμενο."),
    "only the first block with the title": (
        ["<p>Μέθοδος</p><p>Η μέθοδος:</p><p>Μέθοδος</p>"], [[1, "Μέθοδος", 1]],
        "# Μέθοδος\n\nΗ μέθοδος:\n\nΜέθοδος"),
    "title not on the page goes to its top": (["<p>Κείμενο.</p>"], [[1, "Περίληψη", 1]], "# Περίληψη\n\nΚείμενο."),
    "titles not on the page keep their order": (
        ["<p>Κείμενο.</p>"], [[1, "Πρώτο", 1], [2, "Δεύτερο", 1]], "# Πρώτο\n\n## Δεύτερο\n\nΚείμενο."),
    "title of a page with no text": (["<p>Α</p>", ""], [[1, "Παράρτημα", 2]], "Α\n\n# Παράρτημα"),
    "bookmark on the second page": (
        ["<p>Α</p>", "<p>Μέθοδος</p><p>Β</p>"], [[1, "Μέθοδος", 2]], "Α\n\n# Μέθοδος\n\nΒ"),
    "bookmark to no page is skipped": (["<p>Α</p>"], [[1, "Α", 1], [1, "Πουθενά", -1]], "# Α"),
    "title is not part of a longer title": (["<p>Κεφάλαιο 10</p>"], [[1, "Κεφάλαιο 1", 1]], "# Κεφάλαιο 1\n\nΚεφάλαιο 10"),
    "levels deeper than 6 stay at 6": (
        ["".join(f"<p>{c}</p>" for c in "ΑΒΓΔΕΖΗ")], [[i + 1, c, 1] for i, c in enumerate("ΑΒΓΔΕΖΗ")],
        "# Α\n\n## Β\n\n### Γ\n\n#### Δ\n\n##### Ε\n\n###### Ζ\n\n###### Η"),
}


# bookmarks become headings
@pytest.mark.parametrize("pages, toc, expected", TOC_CASES.values(), ids=TOC_CASES.keys())
def test_headings_from_bookmarks(tmp_path, pages, toc, expected):
    assert toc_pdf(tmp_path, pages, toc) == expected


# a bookmark with an empty title adds nothing
def test_empty_bookmark_title_is_skipped(tmp_path):
    assert toc_pdf(tmp_path, ["<p>Α</p>"], [[1, " ", 1]]) == "Α"


# on slides the bookmark is the title of the slide, like pptx
def test_slide_titles_from_bookmarks(tmp_path):
    text = toc_pdf(tmp_path, ["<p>Εισαγωγή</p><p>Κείμενο.</p>", "<p>Β</p>", ""],
                   [[1, "Εισαγωγή", 1], [1, "Τέλος", 3]], LANDSCAPE)
    assert text == "# Slide 1: Εισαγωγή\n\nΚείμενο.\n\n# Slide 2\n\nΒ\n\n# Slide 3: Τέλος"


# the slide title is taken as written on the slide, and a second bookmark is a heading
def test_slide_title_as_written_on_the_slide(tmp_path):
    text = toc_pdf(tmp_path, ["<p>ΕΙΣΑΓΩΓΗ<br>Κείμενο.</p><p>Στόχοι</p>"], [[1, "Εισαγωγη", 1], [2, "Στόχοι", 1]], LANDSCAPE)
    assert text == "# Slide 1: ΕΙΣΑΓΩΓΗ\n\nΚείμενο.\n\n## Στόχοι"


# a chapter title that is also the running header of its pages is removed as a header, then comes back as a heading
def test_heading_that_was_also_a_header(tmp_path):
    w, h = PORTRAIT
    doc = pymupdf.open()
    for body in ["Α", "Β", "Γ"]:
        page = doc.new_page(width=w, height=h)
        page.insert_htmlbox(pymupdf.Rect(50, 20, 400, 60), "<p>Κεφάλαιο 1</p>")
        page.insert_htmlbox(pymupdf.Rect(50, 300, 400, 400), f"<p>{body}</p>")
    doc.set_toc([[1, "Κεφάλαιο 1", 1]])
    f = tmp_path / "chapter.pdf"
    doc.save(f)
    assert pdf_text(f) == "# Κεφάλαιο 1\n\nΑ\n\nΒ\n\nΓ"



SCAN = picture(SENTENCE)
LOGO = picture("Λογότυπο")
WATERMARK = "<p>Σαρωμένο με την εφαρμογή ScanApp Pro, έκδοση 2.4 για Android</p>"
LONG_TEXT = "<p>" + "Κείμενο της σελίδας με αρκετές λέξεις. " * 3 + "</p>"
CLASS = grid([["Όνομα", "Βαθμός"]] + [[name, "9"] for name in ["Μαρία", "Νίκος", "Ελένη", "Γιώργος", "Δήμητρα", "Κατερίνα"]])


# the picture as png bytes, to place it on a pdf page
def png(image):
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


# a pdf whose pages hold, between (top, bottom) as parts of the page height, html text, tables or pictures;
# gives what read_pdf gives: (text, OCR used)
def scan_pdf(tmp_path, pages, size=PORTRAIT, toc=None, ocr=None):
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=size[0], height=size[1])
        for top, bottom, thing in items:
            rect = pymupdf.Rect(50, top * size[1], size[0] - 50, bottom * size[1])
            if isinstance(thing, str):
                page.insert_htmlbox(rect, thing)
            elif isinstance(thing, list):
                draw_table(page, rect.y0, thing)
            else:
                page.insert_image(rect, stream=png(thing))
    if toc:
        doc.set_toc(toc)
    f = tmp_path / "scan.pdf"
    doc.save(f)
    return read_pdf(f) if ocr is None else read_pdf(f, ocr)


# a fixed answer of the OCR, so that only the choices of read_pdf are tested; it keeps what it got
FOUND = ("Το κείμενο που βρήκε το OCR στη σελίδα.", 95.0)


@pytest.fixture
def fake_ocr(monkeypatch):
    fake = {"answer": FOUND, "calls": []}

    def read(image, ocr):
        fake["calls"].append((image, ocr))
        if isinstance(fake["answer"], list):  # one answer for each page, in order
            return fake["answer"].pop(0)
        return fake["answer"]

    monkeypatch.setattr(ocr_module, "ocr_image", read)
    return fake


TEXT_49 = "Μια σελίδα με λίγο κείμενο και ένα λογότυπο εδώ.."
TEXT_50 = "Μια σελίδα με λίγο κείμενο και ένα λογότυπο εδώ..."
CLASS_TEXT = "\n".join(f"Όνομα: {n}; Βαθμός: 9" for n in ["Μαρία", "Νίκος", "Ελένη", "Γιώργος", "Δήμητρα", "Κατερίνα"])
FIRST_PAGE = "<p>Πρώτη σελίδα.</p>"

# (pages, size, what ocr_image gives, what read_pdf gives)
OCR_PAGE_CASES = {
    "scanned page": ([[(0.2, 0.5, SCAN)]], PORTRAIT, FOUND, (FOUND[0], True)),
    "text page is not touched": ([[(0.2, 0.3, FIRST_PAGE)]], PORTRAIT, FOUND, ("Πρώτη σελίδα.", False)),
    "scanned page after a text page": (
        [[(0.2, 0.3, FIRST_PAGE)], [(0.2, 0.5, SCAN)]], PORTRAIT, FOUND, ("Πρώτη σελίδα.\n\n" + FOUND[0], True)),
    "a page number and a scan: OCR replaces the page": (
        [[(0.05, 0.1, "<p>Σελίδα 3</p>"), (0.2, 0.5, SCAN)]], PORTRAIT, FOUND, (FOUND[0], True)),
    "scanned slide": ([[(0.2, 0.6, SCAN)]], LANDSCAPE, FOUND, ("# Slide 1\n\n" + FOUND[0], True)),
    "enough text and a logo: no OCR": ([[(0.2, 0.4, LONG_TEXT), (0.6, 0.7, LOGO)]], PORTRAIT, FOUND,
                                       ("Κείμενο της σελίδας με αρκετές λέξεις. " * 2 + "Κείμενο της σελίδας με αρκετές λέξεις.", False)),
    "little text and no picture: no OCR": (
        [[(0.2, 0.3, "<p>Μια σύντομη σελίδα χωρίς εικόνες.</p>")]], PORTRAIT, FOUND, ("Μια σύντομη σελίδα χωρίς εικόνες.", False)),
    "49 characters and a picture: OCR": ([[(0.2, 0.3, f"<p>{TEXT_49}</p>"), (0.6, 0.7, LOGO)]], PORTRAIT, FOUND, (FOUND[0], True)),
    "50 characters and a picture: no OCR": ([[(0.2, 0.3, f"<p>{TEXT_50}</p>"), (0.6, 0.7, LOGO)]], PORTRAIT, FOUND, (TEXT_50, False)),
    "a table counts as text: no OCR": ([[(0.1, 0.5, CLASS), (0.7, 0.8, LOGO)]], PORTRAIT, FOUND, (CLASS_TEXT, False)),
    "sure but too short: not kept": ([[(0.2, 0.5, SCAN)]], PORTRAIT, ("ΟΚ", 95.0), ("", False)),
    "long but unsure: not kept": (
        [[(0.2, 0.5, SCAN)]], PORTRAIT, ("Ένα κείμενο που μοιάζει μεγάλο αλλά είναι θόρυβος", 30.0), ("", False)),
}


# which pages go to OCR and when their text is kept, right at the limits
@pytest.mark.parametrize("pages, size, found, expected", OCR_PAGE_CASES.values(), ids=OCR_PAGE_CASES.keys())
def test_read_pdf_ocr_of_pages(tmp_path, fake_ocr, pages, size, found, expected):
    fake_ocr["answer"] = found
    assert scan_pdf(tmp_path, pages, size) == expected


# a page drawn only with lines has no picture to read
def test_read_pdf_page_of_lines_only(tmp_path, fake_ocr):
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(100, 100, 300, 300), color=(0, 0, 0))
    f = tmp_path / "lines.pdf"
    doc.save(f)
    assert read_pdf(f) == ("", False)


# a long watermark on every scanned page is a header: once it is removed the page is empty and is read by OCR
def test_read_pdf_scans_with_a_watermark(tmp_path, fake_ocr):
    fake_ocr["answer"] = [(f"Σελίδα {word} του σκαναρίσματος.", 95.0) for word in ["πρώτη", "δεύτερη", "τρίτη"]]
    text, used = scan_pdf(tmp_path, [[(0.02, 0.07, WATERMARK), (0.2, 0.5, SCAN)]] * 3)
    assert used and text.count("του σκαναρίσματος") == 3


# the bookmark title is found in the text that OCR gave
def test_read_pdf_bookmark_in_a_scanned_page(tmp_path, fake_ocr):
    body = "Το εργαστήριο της Python είναι την Τρίτη."
    fake_ocr["answer"] = (f"Εισαγωγή\n\n{body}", 95.0)
    assert scan_pdf(tmp_path, [[(0.2, 0.5, SCAN)]], toc=[[1, "Εισαγωγή", 1]]) == (f"# Εισαγωγή\n\n{body}", True)


# the OCR settings are used: they reach the OCR, and the page picture has the dpi of the config
def test_read_pdf_uses_the_ocr_config(tmp_path, fake_ocr):
    settings = OCRConfig(languages=["eng"], dpi=30)
    scan_pdf(tmp_path, [[(0.2, 0.5, SCAN)]], ocr=settings)
    [(image, ocr)] = fake_ocr["calls"]
    assert ocr is settings and image.width == round(PORTRAIT[0] * 30 / 72)


# a scanned pdf through parse_file and the real OCR: the whole page is read, page number too, with the status ocr
def test_parse_file_scanned_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 40, 545, 85), "<p>Σελίδα 3</p>")
    page.insert_image(pymupdf.Rect(50, 170, 545, 420), stream=png(SCAN))
    scanned = parse_one(tmp_path, "scan.pdf", doc.tobytes())
    assert (scanned.parse_status, scanned.raw_text) == ("ocr", "Σελίδα 3\n\n" + SENTENCE)


# .pdf and the xps formats that pymupdf also opens go through the OCR readers
@pytest.mark.parametrize("ext", [".pdf", ".xps", ".oxps"])
def test_pdf_is_an_ocr_reader(ext):
    assert OCR_PARSERS[ext] is read_pdf and ext not in PARSERS



BODIES = ["Το πρώτο κεφάλαιο ξεκινάει εδώ.", "Η δεύτερη σελίδα συνεχίζει το θέμα.", "Η τρίτη σελίδα έχει ένα παράδειγμα.",
          "Η τέταρτη σελίδα κλείνει το κεφάλαιο."]
UNI = "Πανεπιστήμιο Κρήτης"
DEPT = "Τμήμα Επιστήμης Υπολογιστών"
NOTE = "Σημείωση για όλες τις ασκήσεις"
# pages of five paragraphs, each paragraph different on every page
LONG = [f"Α {w}.\n\nΒ {w}.\n\nΓ {w}.\n\nΔ {w}.\n\nΕ {w}." for w in ["άλφα", "βήτα", "γάμμα", "δέλτα"]]
MIDDLE = [f"Αρχή {w}\n\nΣυνέχεια {w}\n\n{NOTE}\n\nΠαράδειγμα {w}\n\nΤέλος {w}" for w in ["άλφα", "βήτα", "γάμμα", "δέλτα"]]


# a scanned pdf of one picture per page; the fake OCR gives the text of each page, in order
def scanned_book(tmp_path, fake_ocr, pages, text_pages=()):
    fake_ocr["answer"] = [(text, 95.0) for i, text in enumerate(pages) if i not in text_pages]
    layout = [[(0.2, 0.5, f"<p>{pages[i]}</p>")] if i in text_pages else [(0.2, 0.5, SCAN)] for i in range(len(pages))]
    return scan_pdf(tmp_path, layout)[0]


# (text of each scanned page, text after read_pdf)
OCR_HEADER_CASES = {
    "book title on top": (
        [f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}" for b in BODIES], "Αλγόριθμοι και Δομές Δεδομένων\n\n" + "\n\n".join(BODIES)),
    "page number at the bottom": ([f"{b}\n\n{n}" for n, b in enumerate(BODIES, 12)], "\n\n".join(BODIES)),
    "header and page number with words": pytest.param(
        [f"ΚΕΦΑΛΑΙΟ 2\n\n{b}\n\nΣελίδα {n}" for n, b in enumerate(BODIES, 1)], "ΚΕΦΑΛΑΙΟ 2\n\n" + "\n\n".join(BODIES)),
    "two header lines": pytest.param(
        [f"{UNI}\n\n{DEPT}\n\n{b}" for b in BODIES], f"{UNI}\n\n{DEPT}\n\n" + "\n\n".join(BODIES)),
    "watermark at the bottom comes back once, at the top": (
        [f"{b}\n\nΣαρώθηκε με την εφαρμογή ScanApp" for b in BODIES], "Σαρώθηκε με την εφαρμογή ScanApp\n\n" + "\n\n".join(BODIES)),
    "two header lines above a long page": pytest.param(
        [f"{UNI}\n\n{DEPT}\n\n{b}" for b in LONG], f"{UNI}\n\n{DEPT}\n\n" + "\n\n".join(LONG)),
    "two footer lines below a long page": (
        [f"{b}\n\n{UNI}\n\n{DEPT}" for b in LONG], f"{UNI}\n\n{DEPT}\n\n" + "\n\n".join(LONG)),
    "the header is also in the middle of a page: that one stays": (
        [f"{DEPT}\n\n{b}".replace("Β ", f"{DEPT}\n\nΒ ") for b in LONG],
        f"{DEPT}\n\n" + "\n\n".join(b.replace("Β ", f"{DEPT}\n\nΒ ") for b in LONG)),
    "a blank page with only the header loses it": (
        [f"{DEPT}\n\n{b}" for b in BODIES[:3]] + [DEPT], f"{DEPT}\n\n" + "\n\n".join(BODIES[:3])),
    "header on half of the pages": (
        [f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}" if i % 2 else b for i, b in enumerate(BODIES)],
        f"{BODIES[0]}\n\nΑλγόριθμοι και Δομές Δεδομένων\n\n" + "\n\n".join(BODIES[1:])),
}


# lines that repeat at the top or the bottom of the scanned pages are headers and footers, like in P2
@pytest.mark.parametrize("pages, expected", OCR_HEADER_CASES.values(), ids=OCR_HEADER_CASES.keys())
def test_read_pdf_drops_headers_of_scanned_pages(tmp_path, fake_ocr, pages, expected):
    assert scanned_book(tmp_path, fake_ocr, pages) == expected


# (text of each scanned page, text after read_pdf) where nothing may be removed
OCR_KEPT_CASES = {
    "repeated in the middle of the page stays": (MIDDLE, "\n\n".join(MIDDLE)),
    "on fewer than half of the pages it stays": (
        [f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}" if i == 0 else b for i, b in enumerate(BODIES)],
        "Αλγόριθμοι και Δομές Δεδομένων\n\n" + "\n\n".join(BODIES)),
    "copies of the same one paragraph page stay": (["Αίτηση εγγραφής στο εργαστήριο"] * 3, "\n\n".join(["Αίτηση εγγραφής στο εργαστήριο"] * 3)),
    "a line twice on one page counts once": (
        [f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}\n\nΑλγόριθμοι και Δομές Δεδομένων" if i == 0 else b for i, b in enumerate(BODIES)],
        "Αλγόριθμοι και Δομές Δεδομένων\n\n" + BODIES[0] + "\n\nΑλγόριθμοι και Δομές Δεδομένων\n\n" + "\n\n".join(BODIES[1:])),
    "one paragraph pages do not count": (
        [DEPT] * 3 + [f"{DEPT}\n\n{b}" for b in BODIES[:2]], "\n\n".join([DEPT] * 3 + [f"{DEPT}\n\n{b}" for b in BODIES[:2]])),
    "only two scanned pages: nothing is removed": (
        [f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}" for b in BODIES[:2]],
        "\n\n".join(f"Αλγόριθμοι και Δομές Δεδομένων\n\n{b}" for b in BODIES[:2])),
}


# a line repeated in the middle of the page, on too few pages, or in a pdf with too few scanned pages is not a header
@pytest.mark.parametrize("pages, expected", OCR_KEPT_CASES.values(), ids=OCR_KEPT_CASES.keys())
def test_read_pdf_keeps_lines_that_are_not_headers(tmp_path, fake_ocr, pages, expected):
    assert scanned_book(tmp_path, fake_ocr, pages) == expected


# only the scanned pages are counted: a text page with the same line keeps it
def test_read_pdf_ocr_headers_only_on_scanned_pages(tmp_path, fake_ocr):
    title = "Αλγόριθμοι και Δομές Δεδομένων"
    pages = [f"{title}. Πρόλογος του βιβλίου."] + [f"{title}\n\n{b}" for b in BODIES[1:]]
    text = scanned_book(tmp_path, fake_ocr, pages, text_pages={0})
    assert text == f"{title}. Πρόλογος του βιβλίου.\n\n{title}\n\n" + "\n\n".join(BODIES[1:])


# the watermark text of the pages comes back once and does not stop the OCR of the first page
def test_read_pdf_watermark_comes_back_once(tmp_path, fake_ocr):
    fake_ocr["answer"] = [(f"Σελίδα {word} του σκαναρίσματος.", 95.0) for word in ["πρώτη", "δεύτερη", "τρίτη"]]
    text, used = scan_pdf(tmp_path, [[(0.02, 0.07, WATERMARK), (0.2, 0.5, SCAN)]] * 3)
    assert used and text.count("του σκαναρίσματος") == 3 and text.count("ScanApp") == 1


# a title found both in the text of the pages and in the text of OCR comes back only once
def test_read_pdf_title_from_text_and_ocr_comes_back_once(tmp_path, fake_ocr):
    mark = "Σαρωμένο με την εφαρμογή ScanApp Pro, έκδοση 2.4 για Android"
    fake_ocr["answer"] = [(f"{mark}\n\n{b}", 95.0) for b in BODIES[:3]]
    text, _ = scan_pdf(tmp_path, [[(0.02, 0.07, WATERMARK), (0.2, 0.5, SCAN)]] * 3)
    assert text == f"{mark}\n\n" + "\n\n".join(BODIES[:3])


# the same title on scanned pages first and on text pages later comes back once, on the earliest page
def test_read_pdf_title_comes_back_on_its_earliest_page(tmp_path, fake_ocr):
    title = "Αλγόριθμοι και Δομές Δεδομένων"
    fake_ocr["answer"] = [(f"{title}\n\n{b}", 95.0) for b in BODIES[:3]]
    scanned = [[(0.2, 0.5, SCAN)]] * 3
    typed = [[(0.03, 0.08, f"<p>{title}</p>"), (0.3, 0.4, f"<p>{word}</p>")] for word in ["Δ", "Ε", "Ζ"]]
    text, _ = scan_pdf(tmp_path, scanned + typed)
    assert text == f"{title}\n\n" + "\n\n".join(BODIES[:3] + ["Δ", "Ε", "Ζ"])



# a scanned page told apart from the others by its grey, so the fake OCR knows which page it reads
def grey_scan(page):
    return Image.new("RGB", (600, 800), (20 + 5 * page,) * 3)


# the text of a page, with a letter and not a number, so it is not taken for a page number
def body(page):
    return f"Το κείμενο της σελίδας {chr(945 + page)} του βιβλίου."


# a stand-in for OCR that answers by page: text for the pages in "good", rubbish for the rest
@pytest.fixture
def ocr_by_page(monkeypatch):
    fake = {"good": set(), "calls": [], "title": ""}

    def read(image, ocr):
        page = (image.getpixel((image.width // 2, image.height // 2))[0] - 20) // 5
        fake["calls"].append(page)
        if page in fake["good"]:
            return fake["title"] + body(page), 95.0
        return "χ;ζ ψ%ω", 30.0

    monkeypatch.setattr(ocr_module, "ocr_image", read)
    return fake


# a pdf of scanned pages and, at the given places, pages of typed text
def grey_book(tmp_path, count, typed=()):
    pages = [[(0.1, 0.3, LONG_TEXT)] if i in typed else [(0.1, 0.9, grey_scan(i))] for i in range(count)]
    return scan_pdf(tmp_path, pages)


# a pdf where no page of the sample gives text: only the 10 pages of the sample, spread over the pdf, are read
def test_read_pdf_stops_ocr_when_the_sample_has_no_text(tmp_path, ocr_by_page):
    assert grey_book(tmp_path, 30) == ("", False)
    calls = ocr_by_page["calls"]
    assert len(calls) == 10 and len(set(calls)) == 10
    assert min(calls) <= 2 and max(calls) >= 27


# a book whose first 10 pages have no text (cover, blank pages) still gets all its text, every page read once
def test_read_pdf_book_with_empty_first_pages(tmp_path, ocr_by_page):
    ocr_by_page["good"] = set(range(10, 30))
    text, used = grey_book(tmp_path, 30)
    assert (text, used) == ("\n\n".join(body(i) for i in range(10, 30)), True)
    assert sorted(ocr_by_page["calls"]) == list(range(30))


# with 10 scanned pages or fewer, every one is read once, in order
@pytest.mark.parametrize("count", [1, 4, 10])
def test_read_pdf_reads_every_page_of_a_short_scan(tmp_path, ocr_by_page, count):
    assert grey_book(tmp_path, count) == ("", False)
    assert ocr_by_page["calls"] == list(range(count))


# pages with typed text are not in the sample: only scanned pages are
def test_read_pdf_sample_is_only_of_scanned_pages(tmp_path, ocr_by_page):
    typed = range(0, 24, 2)
    text, used = grey_book(tmp_path, 24, typed)
    assert used is False
    calls = ocr_by_page["calls"]
    assert len(calls) == 10 and len(set(calls)) == 10 and not set(calls) & set(typed)


# 11 scanned pages: the sample is 10 of them, and one page with text in the sample makes all 11 read
def test_read_pdf_one_page_of_the_sample_with_text_reads_all(tmp_path, ocr_by_page):
    ocr_by_page["good"] = set(range(11))
    text, used = grey_book(tmp_path, 11)
    assert used is True and text.count("του βιβλίου.") == 11
    assert sorted(ocr_by_page["calls"]) == list(range(11))


# a title on every page but the cover comes back on the first page that has it, though the sample was read first
def test_read_pdf_title_after_the_sample_goes_to_its_first_page(tmp_path, ocr_by_page):
    ocr_by_page["good"] = set(range(1, 30))
    ocr_by_page["title"] = "Αλγόριθμοι και Δομές Δεδομένων\n\n"
    text, _ = grey_book(tmp_path, 30)
    assert text.startswith("Αλγόριθμοι και Δομές Δεδομένων\n\n" + body(1))
    assert text.count("Αλγόριθμοι") == 1


# greek text as an old pdf font gives it: the windows-1253 bytes read as latin-1 ("και" -> "êáé")
def old_greek(text):
    return text.encode("cp1253").decode("latin-1")


# (html of one page, text)
OLD_GREEK_CASES = {
    "greek words": (f"<p>{old_greek('Υπολογίστε το ολοκλήρωμα της συνάρτησης.')}</p>",
                    "Υπολογίστε το ολοκλήρωμα της συνάρτησης."),
    "english words and numbers stay": (f"<p>{old_greek('Η γλώσσα Java είναι στην ενότητα 3.5 (σημείωση).')}</p>",
                                       "Η γλώσσα Java είναι στην ενότητα 3.5 (σημείωση)."),
    "capitals with accent": (f"<p>{old_greek('Άσκηση 2: Ένα πρόγραμμα. Ήταν ίδιο όταν Όλγα, Ύλη, Ώρα.')}</p>",
                             "Άσκηση 2: Ένα πρόγραμμα. Ήταν ίδιο όταν Όλγα, Ύλη, Ώρα."),
    "paragraphs of the page": (f"<p>{old_greek('Πρώτη παράγραφος.')}</p><p>{old_greek('Δεύτερη παράγραφος.')}</p>",
                               "Πρώτη παράγραφος.\n\nΔεύτερη παράγραφος."),
    "a letter that windows-1253 does not have stays": (f"<p>{old_greek('Το γράμμα')} Ò {old_greek('μένει όπως είναι.')}</p>",
                                                       "Το γράμμα Ò μένει όπως είναι."),
    "a letter that only iso-8859-7 has": (f"<p>{old_greek('Το σημάδι')} ª {old_greek('είναι υπογεγραμμένη.')}</p>",
                                          "Το σημάδι ͺ είναι υπογεγραμμένη."),
    "greek page with many english words": (
        f"<p>{old_greek('Η εντολή System.out.println του Java τυπώνει text στο console window.')}</p>",
        "Η εντολή System.out.println του Java τυπώνει text στο console window."),
}


@pytest.mark.parametrize("html, expected", OLD_GREEK_CASES.values(), ids=OLD_GREEK_CASES.keys())
def test_read_pdf_fixes_old_greek(tmp_path, html, expected):
    assert read(tmp_path, [html]) == expected


# (html of one page) text with letters from the top half of latin-1 that is not old greek
NOT_OLD_GREEK = {
    "french and german words in english": "<p>The café in München serves crème brûlée and good coffee every day.</p>",
    "greek that is already right": "<p>Το κείμενο είναι ήδη σωστό, με τόνους και διαλυτικά: ϊ, ΐ.</p>",
    "one odd word in a long page": "<p>Ten words of plain english text and then one odd ÉÈÊ.</p>",
    "a sign alone": "<p>The price is 5 £ or 6 € and the temperature 20 °C.</p>",
    "words with half their letters accented": "<p>Déjà vu, déjà fait, déjà dit: le café est très bon.</p>",
    "a fifth of the words is not enough": "<p>Il a été vu hier.</p>",
    "fractions in a recipe": "<p>Add ¼ cup of milk, ¾ cup of flour, ¼ cup of sugar and ¾ cup of water.</p>",
}


@pytest.mark.parametrize("html", NOT_OLD_GREEK.values(), ids=NOT_OLD_GREEK.keys())
def test_read_pdf_keeps_latin_letters(tmp_path, html):
    assert read(tmp_path, [html]) == pdf_text_of(html)


# the text of the html when it goes through the pdf with no change
def pdf_text_of(html):
    return html.removeprefix("<p>").removesuffix("</p>")


# each page is checked on its own: an old greek page is fixed, a french page next to it is not
def test_read_pdf_fixes_old_greek_page_by_page(tmp_path):
    pages = [f"<p>{old_greek('Η πρώτη σελίδα είναι ελληνικά.')}</p>", "<p>La deuxième page est très française.</p>"]
    assert read(tmp_path, pages) == "Η πρώτη σελίδα είναι ελληνικά.\n\nLa deuxième page est très française."


# a table on an old greek page is fixed too
def test_read_pdf_fixes_old_greek_in_tables(tmp_path):
    table = grid([[old_greek("Όνομα"), old_greek("Βαθμός")], [old_greek("Μαρία"), "9"], [old_greek("Νίκος"), "7"]])
    text = tables_pdf(tmp_path, [[(150, f"<p>{old_greek('Οι βαθμοί του μαθήματος.')}</p>"), (250, table)]])
    assert text == "Οι βαθμοί του μαθήματος.\n\n" + GRADES_TEXT


# a page that is only a table of old greek is fixed
def test_read_pdf_fixes_a_page_that_is_only_an_old_greek_table(tmp_path):
    table = grid([[old_greek("Όνομα"), old_greek("Βαθμός")], [old_greek("Μαρία"), "9"], [old_greek("Νίκος"), "7"]])
    assert tables_pdf(tmp_path, [[(250, table)]]) == GRADES_TEXT


# a line as a font without a table of characters gives it: control characters among capitals and numbers
BROKEN_LINE = "\x00\x00K\x00L\x04\x1b 3.\x00\x00 M(\x08)N, \x00\x00 12 \x00P\x00."
# a line of a LaTeX pdf: a few control characters for the big brackets of a formula
LATEX_LINE = "f(x) = \x10 x + 1 \x11 for x > 0 and \x10 x - 1 \x11 for x < 0, so f is not continuous at 0."


# a pdf whose pages hold html, lines written straight with a font (so control characters stay in the text)
# and tables whose cells are written straight with a font
def font_pdf(tmp_path, pages):
    doc = pymupdf.open()
    for html, lines, cells in pages:
        page = doc.new_page()
        if html:
            page.insert_htmlbox(pymupdf.Rect(50, 50, 500, 300), html)
        for n, line in enumerate(lines):
            page.insert_text((50, 350 + 20 * n), line, fontname="helv")
        if cells:
            draw_table(page, 500, grid([[""] * len(cells[0])] * len(cells)))
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    page.insert_text((53 + c * CELL_W, 500 + r * CELL_H + 15), cell, fontname="helv")
    f = tmp_path / "font.pdf"
    doc.save(f)
    return read_pdf(f)


# a page of text from a broken font goes to OCR, though it has no image and many characters
def test_read_pdf_page_with_a_broken_font_goes_to_ocr(tmp_path, fake_ocr):
    assert font_pdf(tmp_path, [("", [BROKEN_LINE] * 4, None)]) == (FOUND[0], True)
    assert len(fake_ocr["calls"]) == 1


# only the broken page is read, between pages with good text
def test_read_pdf_only_the_broken_page_goes_to_ocr(tmp_path, fake_ocr):
    pages = [("<p>Πρώτη σελίδα.</p>", [], None), ("", [BROKEN_LINE] * 4, None), ("<p>Τρίτη σελίδα.</p>", [], None)]
    assert font_pdf(tmp_path, pages) == ("Πρώτη σελίδα.\n\n" + FOUND[0] + "\n\nΤρίτη σελίδα.", True)
    assert len(fake_ocr["calls"]) == 1


# a broken font in the cells of a table counts too
def test_read_pdf_table_with_a_broken_font_goes_to_ocr(tmp_path, fake_ocr):
    cells = [["\x00K\x00\x04", "\x00\x1b1\x00"], ["\x00M\x08\x00", "\x002\x00\x00"], ["\x00N\x00\x04", "\x003\x08\x00"]]
    text, used = font_pdf(tmp_path, [("", [], cells)])
    assert used is True and len(fake_ocr["calls"]) == 1


# a broken font that puts spaces between its letters: the spaces do not hide how much of the text is broken
def test_read_pdf_broken_font_with_spaces_goes_to_ocr(tmp_path, fake_ocr):
    assert font_pdf(tmp_path, [("", ["\x00 a b c " * 10] * 2, None)]) == (FOUND[0], True)


# when OCR cannot read the broken page either, its text stays as it was
def test_read_pdf_broken_page_keeps_its_text_when_ocr_fails(tmp_path, fake_ocr):
    fake_ocr["answer"] = ("χ;ζ", 20.0)
    text, used = font_pdf(tmp_path, [("", [BROKEN_LINE] * 4, None)])
    assert used is False and "M(" in text


# (html, lines written with a font) pages that keep their text and do not go to OCR
NOT_BROKEN = {
    "latex formulas with a few control characters": ("", [LATEX_LINE] * 3),
    "a column of numbers, one per line": ("<p>" + "<br>".join(str(n) for n in range(1, 41)) + "</p>", []),
    "a line of a broken font in a long page": (
        "<p>" + "Κείμενο της σελίδας με αρκετές σωστές λέξεις για να μετράει. " * 6 + "</p>", [BROKEN_LINE]),
}


@pytest.mark.parametrize("html, lines", NOT_BROKEN.values(), ids=NOT_BROKEN.keys())
def test_read_pdf_page_that_is_not_broken_stays(tmp_path, fake_ocr, html, lines):
    text, used = font_pdf(tmp_path, [(html, lines, None)])
    assert used is False and fake_ocr["calls"] == []


# (pages, page size, text)
BROKEN_ACROSS_CASES = {
    "word broken between two pages": (
        ["<p>Η πρώτη πρόταση. Η πληρο-</p>", "<p>φορία συνεχίζει. Και άλλη.</p>"], PORTRAIT,
        "Η πρώτη πρόταση. Η πληροφορία συνεχίζει. Και άλλη."),
    "word broken between two blocks of a page": (
        ["<p>Η πληρο-</p><p>φορία της σελίδας.</p>"], PORTRAIT, "Η πληροφορία της σελίδας."),
    "hyphen word used elsewhere keeps its hyphen": (
        ["<p>Ένα well-known θέμα, ένα well-</p>", "<p>known άλλο.</p>"], PORTRAIT,
        "Ένα well-known θέμα, ένα well-known άλλο."),
    "next page after the next one": (
        ["<p>Η πληρο-</p>", "<p>φορία δύο.</p>", "<p>Τρίτη σελίδα.</p>"], PORTRAIT,
        "Η πληροφορία δύο.\n\nΤρίτη σελίδα."),
}


@pytest.mark.parametrize("pages, size, expected", BROKEN_ACROSS_CASES.values(), ids=BROKEN_ACROSS_CASES.keys())
def test_read_pdf_joins_a_word_broken_across_blocks(tmp_path, pages, size, expected):
    assert read(tmp_path, pages, size) == expected


# (pages, page size, text) blocks that stay apart
NOT_BROKEN_ACROSS = {
    "capital on the next page": (["<p>Το τέλος με παύλα-</p>", "<p>Κεφάλαιο 3</p>"], PORTRAIT,
                                 "Το τέλος με παύλα-\n\nΚεφάλαιο 3"),
    "number on the next page": (["<p>Το μάθημα ΗΥ-</p>", "<p>340 είναι εδώ.</p>"], PORTRAIT,
                                "Το μάθημα ΗΥ-\n\n340 είναι εδώ."),
    "a dash with a space before it": (["<p>Η τιμή είναι -</p>", "<p>πέντε βαθμοί.</p>"], PORTRAIT,
                                      "Η τιμή είναι -\n\nπέντε βαθμοί."),
    "slides are not joined": (["<p>Η πληρο-</p>", "<p>φορία</p>"], LANDSCAPE,
                              "# Slide 1\n\nΗ πληρο-\n\n# Slide 2\n\nφορία"),
}


@pytest.mark.parametrize("pages, size, expected", NOT_BROKEN_ACROSS.values(), ids=NOT_BROKEN_ACROSS.keys())
def test_read_pdf_keeps_blocks_apart(tmp_path, pages, size, expected):
    assert read(tmp_path, pages, size) == expected


# a table after a broken word is not joined to it, even when its first cell starts with a small letter
def test_read_pdf_does_not_join_a_word_to_a_table(tmp_path):
    table = grid([["όνομα", "βαθμός"], ["Μαρία", "9"]])
    text = tables_pdf(tmp_path, [[(150, "<p>Οι βαθμοί του μαθή-</p>")], [(250, table)]])
    assert text == "Οι βαθμοί του μαθή-\n\nόνομα: Μαρία; βαθμός: 9"


# a table that ends in a hyphen is not joined to the text after it
def test_read_pdf_does_not_join_a_table_to_the_next_text(tmp_path):
    table = grid([["Μάθημα", "Σχόλιο"], ["ΗΥ-340", "καλό-"]])
    text = tables_pdf(tmp_path, [[(150, table), (300, "<p>τατο μάθημα.</p>")]])
    assert text == "Μάθημα: ΗΥ-340; Σχόλιο: καλό-\n\nτατο μάθημα."


# a word broken before a table is joined to the text after the table, not to the table
def test_read_pdf_word_broken_around_a_table_is_left(tmp_path):
    table = grid([["Όνομα", "Βαθμός"], ["Μαρία", "9"]])
    text = tables_pdf(tmp_path, [[(150, "<p>Η πληρο-</p>"), (250, table), (400, "<p>φορία μετά.</p>")]])
    assert text == "Η πληρο-\n\nΌνομα: Μαρία; Βαθμός: 9\n\nφορία μετά."


# (html of one page, text)
CAPITALS_HYPHEN_CASES = {
    "latin acronym": ("<p>Τα PDF-<br>αρχεία ανοίγουν.</p>", "Τα PDF-αρχεία ανοίγουν."),
    "greek acronym": ("<p>Τα ΑΕΙ-<br>ιδρύματα της χώρας.</p>", "Τα ΑΕΙ-ιδρύματα της χώρας."),
    "acronym with a number": ("<p>Ένα MP3-<br>αρχείο ήχου.</p>", "Ένα MP3-αρχείο ήχου."),
    "one capital letter": ("<p>Οι Χ-<br>ακτίνες περνούν.</p>", "Οι Χ-ακτίνες περνούν."),
    "a word all in capitals is joined": ("<p>Η ΠΛΗΡΟ-<br>ΦΟΡΙΑ ΤΟΥ ΤΙΤΛΟΥ</p>", "Η ΠΛΗΡΟΦΟΡΙΑ ΤΟΥ ΤΙΤΛΟΥ"),
}


@pytest.mark.parametrize("html, expected", CAPITALS_HYPHEN_CASES.values(), ids=CAPITALS_HYPHEN_CASES.keys())
def test_read_pdf_keeps_the_hyphen_after_capitals(tmp_path, html, expected):
    assert read(tmp_path, [html]) == expected


# a word that only starts with a capital is joined as before
@pytest.mark.parametrize("html, expected", [
    ("<p>Η Πληρο-<br>φορία αρχίζει.</p>", "Η Πληροφορία αρχίζει."),
    ("<p>Το Java-<br>script τρέχει.</p>", "Το Javascript τρέχει."),
])
def test_read_pdf_joins_a_word_that_starts_with_a_capital(tmp_path, html, expected):
    assert read(tmp_path, [html]) == expected


# two words in capitals with a hyphen, written so elsewhere in the document, keep it
def test_read_pdf_keeps_a_hyphen_between_capitals_used_elsewhere(tmp_path):
    html = "<p>Τα ΑΕΙ-ΤΕΙ και τα ΑΕΙ-<br>ΤΕΙ της χώρας.</p>"
    assert read(tmp_path, [html]) == "Τα ΑΕΙ-ΤΕΙ και τα ΑΕΙ-ΤΕΙ της χώρας."


# an acronym at the end of a page keeps its hyphen with the next page
def test_read_pdf_keeps_the_hyphen_after_capitals_across_pages(tmp_path):
    assert read(tmp_path, ["<p>Τα PDF-</p>", "<p>αρχεία ανοίγουν.</p>"]) == "Τα PDF-αρχεία ανοίγουν."


# latin letters that the OCR read instead of greek are fixed only on the scanned pages: a page with real text keeps
# what was written, but its greek words help to fix the scanned page
def test_parse_file_fixes_lookalikes_only_on_scanned_pages(tmp_path, fake_ocr):
    fake_ocr["answer"] = ("Η άσκηση KAI η εργασία παραδίδονται μαζί.", 95.0)
    scan_pdf(tmp_path, [[(0.2, 0.3, "<p>Η εργασία KAI η άσκηση και οι λύσεις.</p>")], [(0.2, 0.5, SCAN)]])
    doc = parse_one(tmp_path, "mixed.pdf", (tmp_path / "scan.pdf").read_bytes())
    assert (doc.parse_status, doc.raw_text) == (
        "ocr", "Η εργασία KAI η άσκηση και οι λύσεις.\n\nΗ άσκηση ΚΑΙ η εργασία παραδίδονται μαζί.")


# a word broken from a page with text into a scanned page is one word, and the OCR part of it is fixed
def test_parse_file_fixes_lookalikes_of_a_word_broken_into_a_scanned_page(tmp_path, fake_ocr):
    fake_ocr["answer"] = ("ματισμού KAI η λύση της εργασίας.", 95.0)
    scan_pdf(tmp_path, [[(0.2, 0.3, "<p>Η εργασία και η άσκηση του προγραμ-</p>")], [(0.2, 0.5, SCAN)]])
    doc = parse_one(tmp_path, "mixed.pdf", (tmp_path / "scan.pdf").read_bytes())
    assert doc.raw_text == "Η εργασία και η άσκηση του προγραμματισμού ΚΑΙ η λύση της εργασίας."
