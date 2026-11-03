import json
import time

import pytest

from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.parsers.web import html_text
from test_parsers import parse_one

NBSP = chr(0xa0)
FI = chr(0xfb01)


# html as a browser shows it: (html, text)
HTML_CASES = {
    "headings and paragraphs": (
        "<h1>Λογική</h1><p>Πρώτη παράγραφος.</p><h3>Ασκήσεις</h3><p>Δεύτερη.</p>",
        "# Λογική\n\nΠρώτη παράγραφος.\n\n### Ασκήσεις\n\nΔεύτερη."),
    "spaces and new lines count as one space": (
        "<p>Η   εξέταση\n    είναι\tτην Πέμπτη.</p>", "Η εξέταση είναι την Πέμπτη."),
    "line break inside a paragraph": ("<p>Πρώτη γραμμή<br>Δεύτερη γραμμή</p>", "Πρώτη γραμμή\nΔεύτερη γραμμή"),
    "no head, scripts, styles, comments": (
        "<html><head><title>Τίτλος</title><style>p{color:red}</style></head><body><script>var x=1;</script>"
        "<!-- σχόλιο --><p>Κείμενο.</p><noscript>Ενεργοποιήστε JavaScript</noscript></body></html>", "Κείμενο."),
    "link with its address": (
        '<p>Δες τη <a href="https://www.csd.uoc.gr/hy180">σελίδα του μαθήματος</a>.</p>',
        "Δες τη σελίδα του μαθήματος (https://www.csd.uoc.gr/hy180)."),
    "link inside the page": ('<p><a href="#top">Επάνω</a></p>', "Επάνω"),
    "link that is its own address": ('<p><a href="https://www.uoc.gr">www.uoc.gr</a></p>', "www.uoc.gr"),
    "picture with and without alt": (
        '<p>Σχήμα:<img src="a.png" alt="Δέντρο αναζήτησης"></p><img src="b.png">', "[Image: Δέντρο αναζήτησης]\n\nΣχήμα:"),
    "list items": ("<ul><li>Άλφα</li><li>Βήτα <b>έντονα</b></li></ul>", "Άλφα\n\nΒήτα έντονα"),
    "text around blocks": ("Εισαγωγή <p>Μέση.</p> Τέλος", "Εισαγωγή\n\nΜέση.\n\nΤέλος"),
    "pre keeps the code": ("<pre>def main():\n    print(1)\n</pre>", "def main():\n    print(1)"),
    "entities": ("<p>Α &amp; Β &lt; Γ</p>", "Α & Β < Γ"),
    "tags that are not closed": ("<p>Πρώτη<p>Δεύτερη<div>Τρίτη", "Πρώτη\n\nΔεύτερη\n\nΤρίτη"),
    "heading with a link inside the page": ('<h2><a href="#k2">Κεφάλαιο 2</a></h2>', "## Κεφάλαιο 2"),
    "heading of two lines": ("<h1>Πρώτη<br>Δεύτερη</h1>", "# Πρώτη Δεύτερη"),
    "empty page": ("<html><body>  </body></html>", ""),
}


@pytest.mark.parametrize("html, expected", HTML_CASES.values(), ids=HTML_CASES.keys())
def test_html_text(html, expected):
    assert html_text(html) == expected


# tables: (html, text)
TABLE_CASES = {
    "header of th": (
        "<table><tr><th>Όνομα</th><th>Βαθμός</th></tr><tr><td>Μαρία</td><td>9</td></tr>"
        "<tr><td>Νίκος</td><td>7</td></tr></table>",
        "Όνομα: Μαρία; Βαθμός: 9\nΌνομα: Νίκος; Βαθμός: 7"),
    "header in thead": (
        "<table><thead><tr><td>Όνομα</td><td>Βαθμός</td></tr></thead><tbody><tr><td>Μαρία</td><td>9</td></tr></tbody></table>",
        "Όνομα: Μαρία; Βαθμός: 9"),
    "cell over two rows": (
        '<table><tr><th>Εξάμηνο</th><th>Μάθημα</th></tr><tr><td rowspan="2">1ο</td><td>Λογική</td></tr>'
        "<tr><td>Ανάλυση</td></tr></table>",
        "Εξάμηνο: 1ο; Μάθημα: Λογική\nΕξάμηνο: 1ο; Μάθημα: Ανάλυση"),
    "cell over two columns": (
        '<table><tr><th>Όνομα</th><th colspan="2">Βαθμοί</th></tr><tr><td>Μαρία</td><td>9</td><td>8</td></tr></table>',
        "Όνομα: Μαρία; Βαθμοί: 9; Βαθμοί: 8"),
    "cell over rows and columns": (
        '<table><tr><th>Α</th><th>Β</th><th>Γ</th></tr><tr><td colspan="2" rowspan="2">κοινό</td><td>1</td></tr>'
        "<tr><td>2</td></tr></table>",
        "Α: κοινό; Β: κοινό; Γ: 1\nΑ: κοινό; Β: κοινό; Γ: 2"),
    "table inside a cell is not rows of the outer one": (
        "<table><tr><th>Α</th><th>Β</th></tr><tr><td>1</td><td><table><tr><td>x</td><td>y</td></tr></table></td></tr></table>",
        "Α: 1; Β: x | y"),
    "span that is not a number": (
        '<table><tr><th>Α</th><th>Β</th></tr><tr><td colspan="δύο">1</td><td>2</td></tr></table>', "Α: 1; Β: 2"),
    "header in thead that only has numbers": (
        "<table><thead><tr><td>2023</td><td>2024</td></tr></thead><tbody><tr><td>8</td><td>9</td></tr></tbody></table>",
        "2023: 8; 2024: 9"),
    "row of th in the middle is not header": (
        "<table><tr><th>Όνομα</th><th>Βαθμός</th></tr><tr><td>Μαρία</td><td>9</td></tr><tr><th>Σύνολο</th><th>9</th></tr></table>",
        "Όνομα: Μαρία; Βαθμός: 9\nΌνομα: Σύνολο; Βαθμός: 9"),
    "text and table": ("<p>Οι βαθμοί:</p><table><tr><th>Όνομα</th><th>Βαθμός</th></tr><tr><td>Μαρία</td><td>9</td></tr></table>",
                       "Οι βαθμοί:\n\nΌνομα: Μαρία; Βαθμός: 9"),
}


@pytest.mark.parametrize("html, expected", TABLE_CASES.values(), ids=TABLE_CASES.keys())
def test_html_tables(html, expected):
    assert html_text(html) == expected


# a cell that says it covers a huge number of columns is cut to what browsers allow, and takes no time
def test_html_huge_span():
    start = time.monotonic()
    text = html_text('<table><tr><td colspan="100000000">α</td><td>β</td></tr></table>')
    assert time.monotonic() - start < 5
    assert "β" in text and text.count("α") <= 1000


# every html extension goes through the html reader, as prose, and a no-break space becomes a space
@pytest.mark.parametrize("name", ["page.html", "page.htm", "PAGE.XHTML"])
def test_parse_file_html(tmp_path, name):
    doc = parse_one(tmp_path, name, f"<p>Α{NBSP}Β</p>".encode("utf-8"))
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", "Α Β")


# xml: the text between the tags, one line for each element that has text of its own: (xml, text)
XML_CASES = {
    "text of the elements, no tags and no attributes": (
        '<note date="2024-05-01" id="17"><title>Εξέταση Λογικής</title>'
        "<body>Η εξέταση είναι την Πέμπτη στις 10:00.</body></note>",
        "Εξέταση Λογικής\nΗ εξέταση είναι την Πέμπτη στις 10:00."),
    "an element with words in other elements is one line": (
        "<doc><p>Η <b>εξέταση</b> είναι <i>αύριο</i>.</p></doc>", "Η εξέταση είναι αύριο."),
    "an element whose own text comes after another element": (
        "<doc><p><b>Η</b> εξέταση είναι αύριο.</p></doc>", "Η εξέταση είναι αύριο."),
    "spaces and new lines inside an element": ("<r><t>Πρώτη\n      συνέχεια</t></r>", "Πρώτη συνέχεια"),
    "namespaces, cdata, comments and processing instructions": (
        '<?xml version="1.0"?><n:doc xmlns:n="urn:x"><!-- σχόλιο --><?app x?><n:code><![CDATA[if (a < b) return;]]></n:code></n:doc>',
        "if (a < b) return;"),
    "entity of the file itself": ('<!DOCTYPE r [<!ENTITY e "Γεια">]><r><t>&e; σου</t></r>', "Γεια σου"),
}


@pytest.mark.parametrize("xml, expected", XML_CASES.values(), ids=XML_CASES.keys())
def test_parse_file_xml(tmp_path, xml, expected):
    doc = parse_one(tmp_path, "notes.xml", xml.encode("utf-8"))
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", expected)


# the encoding written in the first line of the xml is used
def test_parse_file_xml_greek_encoding(tmp_path):
    data = '<?xml version="1.0" encoding="iso-8859-7"?><r><t>Γεια σου</t></r>'.encode("iso-8859-7")
    assert parse_one(tmp_path, "notes.xml", data).raw_text == "Γεια σου"


# settings of programs keep everything in attributes: nothing to read
def test_parse_file_xml_only_attributes(tmp_path):
    data = b'<section name="Workbench"><item key="width" value="800"/><item key="height" value="600"/></section>'
    doc = parse_one(tmp_path, "dialog_settings.xml", data)
    assert (doc.parse_status, doc.raw_text) == ("empty", "")


# an xml that is broken, or that grows by expanding entities (billion laughs), fails
BILLION_LAUGHS = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                  + "".join(f'<!ENTITY lol{i} "{("&lol" + (str(i - 1) if i > 1 else "") + ";") * 10}">' for i in range(1, 10))
                  + "]><lolz>&lol9;</lolz>")


@pytest.mark.parametrize("data", [b"<r><t>open</r>", b"not xml at all", BILLION_LAUGHS.encode()],
                         ids=["broken", "not xml", "billion laughs"])
def test_parse_file_xml_that_fails(tmp_path, data):
    start = time.monotonic()
    doc = parse_one(tmp_path, "notes.xml", data)
    assert (doc.parse_status, doc.raw_text) == ("failed", "")
    assert time.monotonic() - start < 5


# an entity that points to a file of the computer is never read
def test_parse_file_xml_external_entity(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("μυστικό", encoding="utf-8")
    data = f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]><r><t>&x;</t></r>'.encode()
    assert "μυστικό" not in parse_one(tmp_path, "notes.xml", data).raw_text


# notebooks
def notebook(*cells):
    return json.dumps({"cells": list(cells), "metadata": {}, "nbformat": 4, "nbformat_minor": 5}, ensure_ascii=False).encode()


def markdown(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text, *outputs):
    return {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": text, "outputs": list(outputs)}


def stream(text, name="stdout"):
    return {"output_type": "stream", "name": name, "text": text.splitlines(keepends=True)}


def result(data, kind="execute_result"):
    return {"output_type": kind, "data": data, "metadata": {}}


PANDAS = ('<div><style scoped>.dataframe tbody tr th {vertical-align: top;}</style><table border="1" class="dataframe">'
          "<thead><tr style=\"text-align: right;\"><th></th><th>Όνομα</th><th>Βαθμός</th></tr></thead>"
          "<tbody><tr><th>0</th><td>Μαρία</td><td>9</td></tr><tr><th>1</th><td>Νίκος</td><td>7</td></tr></tbody></table></div>")
PANDAS_PLAIN = "   Όνομα  Βαθμός\n0  Μαρία       9\n1  Νίκος       7"

# (cells, text)
NOTEBOOK_CASES = {
    "cells in order and the output after its code": (
        [markdown("# Εργασία 1\nΔιαβάζουμε τα **δεδομένα**."), code("x = 1 + 1\nprint(x)", stream("2\n")),
         markdown("Το αποτέλεσμα είναι σωστό.")],
        "# Εργασία 1\n\nΔιαβάζουμε τα δεδομένα.\n\nx = 1 + 1\nprint(x)\n\n2\n\nΤο αποτέλεσμα είναι σωστό."),
    "code keeps its lines and spaces": (
        [code("def f(x):\n    if x:\n        return 1\n")], "def f(x):\n    if x:\n        return 1"),
    "code that starts with spaces keeps them": ([code("    return x\n")], "    return x"),
    "result as text": ([code("acc", result({"text/plain": "Ακρίβεια: 0.93"}))], "acc\n\nΑκρίβεια: 0.93"),
    "pandas table from its html, not twice": (
        [code("df", result({"text/html": PANDAS, "text/plain": PANDAS_PLAIN}))],
        "df\n\ncol1: 0; Όνομα: Μαρία; Βαθμός: 9\ncol1: 1; Όνομα: Νίκος; Βαθμός: 7"),
    "no warnings, errors or pictures": (
        [code("plot()", stream("FutureWarning: old\n", "stderr"),
              {"output_type": "error", "ename": "ValueError", "evalue": "κακό", "traceback": ["Traceback"]},
              result({"image/png": "iVBORw0KGgo=", "text/plain": "<Figure size 640x480 with 1 Axes>"}, "display_data"))],
        "plot()"),
    "display of text": ([code("show()", result({"text/plain": "Τέλος εκπαίδευσης"}, "display_data"))],
                        "show()\n\nΤέλος εκπαίδευσης"),
    "widget of html without text": (
        [code("w", result({"text/html": "<div id='w'></div><script>render()</script>", "text/plain": "HBox(children=())"}))], "w"),
    "raw cell and empty cells": (
        [{"cell_type": "raw", "metadata": {}, "source": "Κείμενο raw."}, markdown(""), code("")], "Κείμενο raw."),
    "source as one string or as lines": (
        [{"cell_type": "markdown", "metadata": {}, "source": "Μία γραμμή.\nΚαι άλλη."}, markdown("Μία γραμμή.\nΚαι άλλη.")],
        "Μία γραμμή.\nΚαι άλλη.\n\nΜία γραμμή.\nΚαι άλλη."),
    "picture attached to a markdown cell": ([markdown("![Διάγραμμα ροής](attachment:image.png)")], "[Image: Διάγραμμα ροής]"),
}


@pytest.mark.parametrize("cells, expected", NOTEBOOK_CASES.values(), ids=NOTEBOOK_CASES.keys())
def test_parse_file_notebook(tmp_path, cells, expected):
    doc = parse_one(tmp_path, "lab.ipynb", notebook(*cells))
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", expected)


# json that is not a notebook, or not json at all, fails
@pytest.mark.parametrize("data", [b'{"a": 1}', b'{"cells": "none"}', b"[1, 2]", b"{not json"],
                         ids=["no cells", "cells not a list", "a list", "broken"])
def test_parse_file_notebook_that_fails(tmp_path, data):
    assert parse_one(tmp_path, "lab.ipynb", data).parse_status == "failed"


# json is read as it is written, like code: no character is changed
def test_parse_file_json(tmp_path):
    text = '{"dependencies": {"com.unity.ugui": "1.0.0"}, "name": "' + FI + "le" + NBSP + 'x"}'
    doc = parse_one(tmp_path, "manifest.json", text.encode("utf-8"))
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "code", text)


# the folder that NetBeans makes for every project holds only its settings
def test_nbproject_is_ignored(tmp_path):
    root = tmp_path / "corpus"
    (root / "Lab1" / "nbproject").mkdir(parents=True)
    (root / "Lab1" / "nbproject" / "project.xml").write_text("<project/>", encoding="utf-8")
    (root / "Lab1" / "Main.java").write_text("class Main {}", encoding="utf-8")
    result = scan(CorpusConfig(roots=[root]))
    assert [f.rel_path.as_posix() for f in result.files] == ["Lab1/Main.java"]
    assert [(p.name, r) for p, r in result.ignored] == [("nbproject", "ignored_dir")]


# epub (D98): a zip of XHTML pages; the book is the pages of the spine, in its order

CONTAINER = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="{opf}" media-type="application/oebps-package+xml"/></rootfiles></container>')


def page(body, prolog=True):
    head = '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>' if prolog else ""
    return head + f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>{body}</body></html>'


# an epub at f: `pages` {href inside the OPF folder: xhtml}, `spine` the hrefs in reading order
def make_epub(f, pages, spine, opf="OEBPS/content.opf", container=True, encrypted=(), extra=None):
    import zipfile
    from urllib.parse import quote
    folder = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
    ids = {href: f"item{i}" for i, href in enumerate(pages)}
    manifest = "".join(f'<item id="{ids[h]}" href="{quote(h)}" media-type="application/xhtml+xml"/>' for h in pages)
    itemrefs = "".join(f'<itemref idref="{ids[h]}"/>' for h in spine)
    package = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Βιβλίο</dc:title></metadata>'
               f'<manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>')
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        if container:
            z.writestr("META-INF/container.xml", CONTAINER.format(opf=opf))
        z.writestr(opf, package)
        for href, xhtml in pages.items():
            z.writestr(folder + href, xhtml.encode("utf-8"))
        if encrypted:
            refs = "".join(f'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="{folder + h}"/>'
                           "</CipherData></EncryptedData>" for h in encrypted)
            z.writestr("META-INF/encryption.xml", f'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">{refs}</encryption>')
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def epub_doc(tmp_path, **book):
    f = tmp_path / "made.epub"
    make_epub(f, **book)
    return parse_one(tmp_path, "book.epub", f.read_bytes())


EPUB_CASES = {
    "pages in the order of the spine, not of the zip": (
        dict(pages={"a.xhtml": page("<p>Δεύτερο.</p>"), "b.xhtml": page("<p>Πρώτο.</p>")}, spine=["b.xhtml", "a.xhtml"]),
        "Πρώτο.\n\nΔεύτερο."),
    "headings and paragraphs like html": (
        dict(pages={"c1.xhtml": page("<h1>Κεφάλαιο 1</h1><p>Κείμενο.</p><h2>Ενότητα</h2><p>Άλλο.</p>")}, spine=["c1.xhtml"]),
        "# Κεφάλαιο 1\n\nΚείμενο.\n\n## Ενότητα\n\nΆλλο."),
    "the xml prolog and doctype are not text": (
        dict(pages={"c1.xhtml": page("<p>Μόνο αυτό.</p>")}, spine=["c1.xhtml"]), "Μόνο αυτό."),
    "pages in a folder next to the OPF": (
        dict(pages={"text/c1.xhtml": page("<p>Μέσα σε φάκελο.</p>")}, spine=["text/c1.xhtml"]), "Μέσα σε φάκελο."),
    "a name with a space, written %20 in the OPF": (
        dict(pages={"ch 1.xhtml": page("<p>Με κενό.</p>")}, spine=["ch 1.xhtml"]), "Με κενό."),
    "the OPF at the top of the zip": (
        dict(pages={"c1.xhtml": page("<p>Χωρίς φάκελο.</p>")}, spine=["c1.xhtml"], opf="content.opf"), "Χωρίς φάκελο."),
    "pages that are not in the spine are not read": (
        dict(pages={"c1.xhtml": page("<p>Κείμενο.</p>"), "nav.xhtml": page("<nav><p>Περιεχόμενα</p></nav>")}, spine=["c1.xhtml"]),
        "Κείμενο."),
    "fonts made unreadable on purpose (not the text) are fine": (
        dict(pages={"c1.xhtml": page("<p>Κείμενο.</p>")}, spine=["c1.xhtml"], encrypted=["fonts/a.otf"]), "Κείμενο."),
}


@pytest.mark.parametrize("book, text", EPUB_CASES.values(), ids=EPUB_CASES.keys())
def test_read_epub(tmp_path, book, text):
    doc = epub_doc(tmp_path, **book)
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", text)


# a spine item that the manifest has but the zip does not: the rest of the book is read
def test_read_epub_missing_page(tmp_path):
    import zipfile
    f = tmp_path / "made.epub"
    make_epub(f, {"c1.xhtml": page("<p>Πρώτο.</p>"), "c2.xhtml": page("<p>Δεύτερο.</p>")}, ["c1.xhtml", "c2.xhtml"])
    with zipfile.ZipFile(f) as z:
        kept = {n: z.read(n) for n in z.namelist() if not n.endswith("c1.xhtml")}
    with zipfile.ZipFile(f, "w") as z:
        for n, data in kept.items():
            z.writestr(n, data)
    doc = parse_one(tmp_path, "book.epub", f.read_bytes())
    assert (doc.parse_status, doc.raw_text) == ("ok", "Δεύτερο.")


# what cannot be read is failed: no container, not a zip, text locked with DRM
@pytest.mark.parametrize("make", [
    lambda f: make_epub(f, {"c1.xhtml": page("<p>x</p>")}, ["c1.xhtml"], container=False),
    lambda f: f.write_bytes(b"not a zip at all"),
    lambda f: make_epub(f, {"c1.xhtml": page("<p>x</p>")}, ["c1.xhtml"], encrypted=["c1.xhtml"]),
], ids=["no container", "not a zip", "drm on the text"])
def test_read_epub_failed(tmp_path, make):
    f = tmp_path / "made.epub"
    make(f)
    doc = parse_one(tmp_path, "book.epub", f.read_bytes())
    assert (doc.parse_status, doc.raw_text) == ("failed", "")


# a book with an empty spine has no text
def test_read_epub_empty(tmp_path):
    doc = epub_doc(tmp_path, pages={"c1.xhtml": page("<p>x</p>")}, spine=[])
    assert doc.parse_status == "empty"
