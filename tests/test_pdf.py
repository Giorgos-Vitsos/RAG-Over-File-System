import pymupdf
import pytest

from rag_fs.ingest.parsers import PARSERS
from rag_fs.ingest.parsers.pdf import read_pdf
from test_parsers import parse_one

PORTRAIT = (595, 842)
LANDSCAPE = (842, 595)


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
    return read_pdf(f)


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
    assert read_pdf(f) == "Α\n\nΒ"


# .pdf and the xps formats that pymupdf also opens
@pytest.mark.parametrize("ext", [".pdf", ".xps", ".oxps"])
def test_pdf_extensions_are_registered(ext):
    assert PARSERS.get(ext) is read_pdf


# through parse_file: text, scanned (no text), password, broken file
@pytest.mark.parametrize("name, data, status, text", [
    ("notes.pdf", pdf_bytes(["<p>Σημειώσεις.</p>"]), "ok", "Σημειώσεις."),
    ("SCAN.PDF", pdf_bytes(["", ""]), "empty", ""),
    ("broken.pdf", b"%PDF-1.7 this is not a real pdf", "failed", ""),
])
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
    return read_pdf(f)


THREE = ["<p>Α</p>", "<p>Β</p>", "<p>Γ</p>"]

# (bodies, headers, footers, text)
EDGE_CASES = {
    "page x of y footer": (THREE, None, ["<p>Σελίδα {n} από 3</p>"] * 3, "Α\n\nΒ\n\nΓ"),
    "page number alone": (THREE, None, ["<p>{n}</p>"] * 3, "Α\n\nΒ\n\nΓ"),
    "header on every page": (THREE, ["<p>ΗΥ-340 Μεταφραστές</p>"] * 3, None, "Α\n\nΒ\n\nΓ"),
    "header and footer": (THREE, ["<p>ΗΥ-340</p>"] * 3, ["<p>- {n} -</p>"] * 3, "Α\n\nΒ\n\nΓ"),
    "spaces and capitals do not matter": (
        THREE, ["<p>ΗΥ-340 Μεταφραστές</p>", "<p>ΗΥ-340&nbsp; Μεταφραστές</p>", "<p>ηυ-340 μεταφραστές</p>"], None,
        "Α\n\nΒ\n\nΓ"),
    "footer on half of the pages": (
        THREE + ["<p>Δ</p>"], None, ["<p>Πρόχειρο</p>", None, "<p>Πρόχειρο</p>", None], "Α\n\nΒ\n\nΓ\n\nΔ"),
    "page with only a footer becomes empty": (
        ["<p>Α</p>", "", "<p>Γ</p>"], None, ["<p>{n}</p>"] * 3, "Α\n\nΓ"),
    "header text in the body is kept": (
        ["<p>Το ΗΥ-340 είναι μάθημα.</p>", "<p>ΗΥ-340</p>", "<p>Γ</p>"], ["<p>ΗΥ-340</p>"] * 3, None,
        "Το ΗΥ-340 είναι μάθημα.\n\nΗΥ-340\n\nΓ"),
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
    return read_pdf(f)


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
    return read_pdf(f)


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
