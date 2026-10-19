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
