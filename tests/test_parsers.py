import datetime
import logging
import zipfile
from pathlib import Path

import docx
import openpyxl
import pandas as pd
import pptx
import pytest
from docx.shared import Inches
from PIL import Image
from pptx.shapes.autoshape import Shape as PptxShape
from pptx.util import Inches as PptxInches

from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.parsers import PARSERS, parse_file
from rag_fs.ingest.parsers.office import (
    format_shape, heading_level, read_docx, read_pptx, read_word_variants,
)
from rag_fs.ingest.parsers.tabular import (
    cell_to_str, format_rows, read_csv, read_ods, read_xls, read_xlsx,
)
from rag_fs.ingest.parsers.text import read_text

FIXTURES = Path(__file__).parent / "fixtures"

ALLOWED_STATUSES = {"ok", "ocr", "empty", "failed", "unsupported"}
ALLOWED_CONTENT_TYPES = {"prose", "code", "tabular", "binary"}


def parse_all(tmp_path, files):
    """Write the given {name: bytes} files, scan them, and parse every one.
    Returns {name: Document}."""
    root = tmp_path / "corpus"
    root.mkdir()
    for name, data in files.items():
        (root / name).write_bytes(data)
    result = scan(CorpusConfig(roots=[root]))
    docs = {}
    for scanned in result.files:
        docs[scanned.rel_path.name] = parse_file(scanned)
    return docs


@pytest.fixture
def docs(tmp_path):
    return parse_all(tmp_path, {
        "notes.txt": "Γεια\r\nσου\rκόσμε".encode("utf-8"),
        "main.py": b"print(1)\n",
        "blank.md": b"  \n\n",
        "data.zip": b"PK\x03\x04",
        "fake.txt": b"ELF\x00\x00\x01",
        "Makefile": b"all:\n\tgcc a.c\n",
        "weird.xyz": bytes(range(256)) * 4,
        "old.txt": "Λογαριασμός ρεύματος".encode("cp1253"),
    })


def test_text_file_is_ok_and_line_endings_are_normalized(docs):
    doc = docs["notes.txt"]
    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == "Γεια\nσου\nκόσμε"


def test_code_file_has_code_content_type(docs):
    assert docs["main.py"].content_type == "code"
    assert docs["main.py"].parse_status == "ok"


def test_whitespace_only_file_is_empty(docs):
    assert docs["blank.md"].parse_status == "empty"


def test_known_binary_extension_is_unsupported(docs):
    doc = docs["data.zip"]
    assert doc.parse_status == "unsupported"
    assert doc.content_type == "binary"
    assert doc.raw_text == ""


def test_known_text_extension_that_is_binary_is_failed(docs):
    doc = docs["fake.txt"]
    assert doc.parse_status == "failed"
    assert doc.content_type == "prose"
    assert doc.raw_text == ""


def test_unknown_extension_that_is_binary_is_unsupported(docs):
    doc = docs["weird.xyz"]
    assert doc.parse_status == "unsupported"
    assert doc.content_type == "binary"


def test_file_without_extension_is_read_as_text(docs):
    doc = docs["Makefile"]
    assert doc.ext == ""
    assert doc.parse_status == "ok"
    assert "gcc" in doc.raw_text


def test_old_greek_encoding_through_parse_file(docs):
    assert docs["old.txt"].raw_text == "Λογαριασμός ρεύματος"


def test_document_fields_come_from_the_scanned_file(tmp_path):
    root = tmp_path / "corpus"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hello")
    scanned = scan(CorpusConfig(roots=[root])).files[0]

    doc = parse_file(scanned)

    assert doc.file_path == Path("sub/a.txt")
    assert doc.root_id == scanned.root_id
    assert doc.sha256 == scanned.sha256
    assert doc.ext == ".txt"


def test_only_known_labels_are_used(docs):
    for doc in docs.values():
        assert doc.parse_status in ALLOWED_STATUSES
        assert doc.content_type in ALLOWED_CONTENT_TYPES


def test_failure_is_logged_not_raised(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        docs = parse_all(tmp_path, {"fake.txt": b"\x00\x01\x02"})

    assert docs["fake.txt"].parse_status == "failed"
    assert "fake.txt" in caplog.text


@pytest.mark.parametrize(
    "text, encoding",
    [
        ("Λογαριασμός ρεύματος Μαρτίου: 54€", "utf-8"),
        ("Ο λογαριασμός του ρεύματος για τον Μάρτιο ήταν 54 ευρώ.", "cp1253"),
        ("Λογαριασμός ρεύματος", "cp1253"),
        ("Σημειώσεις", "cp1253"),
        ("Café crème, déjà vu à Paris.", "cp1252"),
        ("Γεια σου κόσμε", "utf-16"),
    ],
)
def test_read_text_decodes_common_encodings(tmp_path, text, encoding):
    f = tmp_path / "file.txt"
    f.write_bytes(text.encode(encoding))

    assert read_text(f) == text


def test_read_text_removes_bom(tmp_path):
    f = tmp_path / "bom.txt"
    f.write_bytes("﻿Γεια σου".encode("utf-8"))

    assert read_text(f) == "Γεια σου"


def test_read_text_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")

    assert read_text(f) == ""


def test_read_text_keeps_line_endings(tmp_path):
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"a\r\nb")

    assert read_text(f) == "a\r\nb"


@pytest.mark.parametrize(
    "data",
    [
        bytes(range(256)) * 4,
        b"ELF\x00\x00\x01abc",
    ],
)
def test_read_text_rejects_binary(tmp_path, data):
    f = tmp_path / "file.bin"
    f.write_bytes(data)

    with pytest.raises(ValueError):
        read_text(f)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, ""),
        (datetime.datetime(2024, 3, 1), "2024-03-01"),
        (datetime.datetime(2024, 3, 1, 14, 30), "2024-03-01 14:30:00"),
        (54, "54"),
        (54.0, "54"),         
        (12.5, "12.5"),
        ("  Ρεύμα  ", "Ρεύμα"),
        ("line one\nline two", "line one / line two"),
    ],
)
def test_cell_to_str(value, expected):
    assert cell_to_str(value) == expected


def test_simple_table_first_row_is_header():
    rows = [
        ("Ημερομηνία", "Κατηγορία", "Ποσό"),
        (datetime.datetime(2024, 3, 1), "Ρεύμα", 54),
        (None, None, None),
        ("2024-04-01", "Νερό", 12.5),
    ]
    assert format_rows(rows) == (
        "Ημερομηνία: 2024-03-01; Κατηγορία: Ρεύμα; Ποσό: 54\n"
        "Ημερομηνία: 2024-04-01; Κατηγορία: Νερό; Ποσό: 12.5"
    )


def test_title_rows_above_the_header_are_kept_as_text():
    N = None
    rows = [
        [N, N, N, N, N, N, N],
        [N, "CS-209: English IV, Spring 2026", N, N, N, N, N],
        [N, N, "TEAM MEMBERS", "Paper", N, "Phases", N],
        [N, "TEAM #", "NAMES & AM", "Link", "A+B", "C+D", "TOTAL"],
        [N, 1, "Aggelos Papanikolaou - 5601 (leader)\nEirini Lyroni - 5690",
         "https://doi.org/10.1145/3635636.3656185", N, N, N],
    ]
    assert format_rows(rows) == (
        "CS-209: English IV, Spring 2026\n"
        "TEAM MEMBERS | Paper | Phases\n"
        "TEAM #: 1; NAMES & AM: Aggelos Papanikolaou - 5601 (leader) / Eirini Lyroni - 5690; "
        "Link: https://doi.org/10.1145/3635636.3656185"
    )


def test_empty_header_cell_gets_a_column_name():
    rows = [("Ημερομηνία", "Κατηγορία", None), ("2024-03-15", None, 20)]
    assert format_rows(rows) == "Ημερομηνία: 2024-03-15; col3: 20"


def test_short_rows_do_not_crash():
    rows = [("A", "B", "C"), ("1",)]
    assert format_rows(rows) == "A: 1"


def test_only_header_is_kept():
    assert format_rows([("Προϊόν", "Ποσότητα", "Τιμή")]) == "Προϊόν | Ποσότητα | Τιμή"


def test_single_column_is_not_treated_as_a_table():
    rows = [("Ψώνια",), ("γάλα",), ("ψωμί",)]
    assert format_rows(rows) == "Ψώνια\nγάλα\nψωμί"


def test_empty_sheet_gives_empty_text():
    assert format_rows([]) == ""
    assert format_rows([(None, None), ("", "")]) == ""


def make_xlsx(path, sheets):
    """Write an .xlsx file. `sheets` is a list of (sheet name, rows)."""
    book = openpyxl.Workbook()
    book.remove(book.worksheets[0]) 
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(path)


def make_ods(path, sheets):
    """Write an .ods file. `sheets` is a list of (sheet name, rows)."""
    with pd.ExcelWriter(path, engine="odf") as writer:
        for name, rows in sheets:
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, header=False, index=False)


EXPENSES = [
    ["Ημερομηνία", "Κατηγορία", "Ποσό"],
    [datetime.datetime(2024, 3, 1), "Ρεύμα", 54],
]
INCOME = [["Μήνας", "Ποσό"], ["Μάρτιος", 1200]]

EXPECTED_TWO_SHEETS = (
    "# Sheet: Έξοδα\n"
    "Ημερομηνία: 2024-03-01; Κατηγορία: Ρεύμα; Ποσό: 54\n"
    "\n"
    "# Sheet: Έσοδα\n"
    "Μήνας: Μάρτιος; Ποσό: 1200"
)


def test_read_xlsx_all_sheets_and_skips_empty_ones(tmp_path):
    f = tmp_path / "book.xlsx"
    make_xlsx(f, [("Έξοδα", EXPENSES), ("Κενό", []), ("Έσοδα", INCOME)])

    assert read_xlsx(f) == EXPECTED_TWO_SHEETS


def test_read_ods_all_sheets(tmp_path):
    f = tmp_path / "book.ods"
    rows = [["Ημερομηνία", "Κατηγορία", "Ποσό"], ["2024-03-01", "Ρεύμα", 54]]
    make_ods(f, [("Έξοδα", rows), ("Έσοδα", INCOME)])

    assert read_ods(f) == EXPECTED_TWO_SHEETS


@pytest.mark.parametrize(
    "name, data, expected",
    [
        (  
            "greek.csv",
            "Ημερομηνία;Κατηγορία;Ποσό\n2024-03-01;Ρεύμα;54,5\n".encode("cp1253"),
            "Ημερομηνία: 2024-03-01; Κατηγορία: Ρεύμα; Ποσό: 54,5",
        ),
        (   
            "address.csv",
            b'date,address,amount\n2024-03-01,"Knossou 5, Heraklion",54\n',
            "date: 2024-03-01; address: Knossou 5, Heraklion; amount: 54",
        ),
        (
            "table.tsv",
            b"a\tb\n1\t2\n",
            "a: 1; b: 2",
        ),
        (   
            "names.csv",
            b"name\nmaria\ngiorgos\n",
            "name\nmaria\ngiorgos",
        ),
    ],
)
def test_read_csv(tmp_path, name, data, expected):
    f = tmp_path / name
    f.write_bytes(data)

    assert read_csv(f) == expected


def test_read_xlsx_raises_on_a_broken_file(tmp_path):
    f = tmp_path / "broken.xlsx"
    f.write_bytes(b"this is not a zip file")

    with pytest.raises(Exception):
        read_xlsx(f)


def test_parse_file_uses_the_tabular_readers(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    make_xlsx(root / "book.xlsx", [("Έξοδα", EXPENSES)])
    make_ods(root / "book.ods", [("Έσοδα", INCOME)])
    (root / "data.csv").write_bytes(b"a;b\n1;2\n")

    docs = {}
    for scanned in scan(CorpusConfig(roots=[root])).files:
        docs[scanned.rel_path.name] = parse_file(scanned)

    assert docs["book.xlsx"].raw_text.startswith("# Sheet: Έξοδα")
    assert docs["book.ods"].raw_text.startswith("# Sheet: Έσοδα")
    assert docs["data.csv"].raw_text == "a: 1; b: 2"
    for doc in docs.values():
        assert doc.content_type == "tabular"
        assert doc.parse_status == "ok"


def test_parse_file_marks_a_broken_xlsx_as_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.xlsx").write_bytes(b"this is not a zip file")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "failed"
    assert doc.content_type == "tabular"


EXPECTED_XLS = (
    "# Sheet: Έξοδα\n"
    "Ημερομηνία: 2024-03-01; Κατηγορία: Ρεύμα; Ποσό: 54\n"
    "Ημερομηνία: 2024-04-01; Κατηγορία: Νερό; Ποσό: 12.5\n"
    "\n"
    "# Sheet: Έσοδα\n"
    "Μήνας: Μάρτιος; Ποσό: 1200"
)


def test_read_xls_dates_numbers_and_sheets():
    assert read_xls(FIXTURES / "expenses.xls") == EXPECTED_XLS


def test_parse_file_reads_xls(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "expenses.xls").write_bytes((FIXTURES / "expenses.xls").read_bytes())

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "tabular"
    assert doc.raw_text == EXPECTED_XLS


@pytest.mark.parametrize(
    "style, level",
    [
        ("Title", 1),
        ("Heading 1", 1),
        ("Heading 2", 2),
        ("Heading 4", 4),
        ("Heading 9", 9),
        ("Normal", 0),
        ("Caption", 0),
        ("Heading", 0),       
        ("Heading Char", 0),   
    ],
)
def test_heading_level(style, level):
    assert heading_level(style) == level


def make_docx(path):
    """A Word document with every case read_docx has to handle."""
    image = path.parent / "img.png"
    Image.new("RGB", (60, 30), "white").save(image)

    doc = docx.Document()
    doc.add_heading("Εισαγωγή", level=1)
    doc.add_paragraph("Πρώτη παράγραφος.\nΜε δεύτερη γραμμή.")
    doc.add_paragraph("")
    doc.add_paragraph("   ")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Όνομα", "Βαθμός"
    table.cell(1, 0).text, table.cell(1, 1).text = "Μαρία", "9"
    doc.add_heading("Μέθοδος", level=2)
    doc.add_picture(str(image), width=Inches(1))
    doc.inline_shapes[-1]._inline.docPr.set("descr", "Αρχιτεκτονική του συστήματος")
    doc.add_picture(str(image), width=Inches(1))   
    doc.add_paragraph("Εικόνα 1: Η ροή των δεδομένων", style="Caption")
    doc.add_heading("Λεπτομέρειες", level=4)
    doc.add_paragraph("Τελευταία παράγραφος.")
    doc.save(str(path))


EXPECTED_DOCX = (
    "# Εισαγωγή\n"
    "\n"
    "Πρώτη παράγραφος.\nΜε δεύτερη γραμμή.\n"
    "\n"
    "Όνομα: Μαρία; Βαθμός: 9\n"
    "\n"
    "## Μέθοδος\n"
    "\n"
    "[Image: Αρχιτεκτονική του συστήματος]\n"
    "\n"
    "Εικόνα 1: Η ροή των δεδομένων\n"
    "\n"
    "#### Λεπτομέρειες\n"
    "\n"
    "Τελευταία παράγραφος."
)


def test_read_docx_headings_tables_images_in_order(tmp_path):
    f = tmp_path / "doc.docx"
    make_docx(f)

    assert read_docx(f) == EXPECTED_DOCX


def test_read_docx_empty_document(tmp_path):
    f = tmp_path / "empty.docx"
    docx.Document().save(str(f))

    assert read_docx(f) == ""


def test_parse_file_reads_docx(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    make_docx(root / "doc.docx")
    (root / "img.png").unlink()   

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_DOCX


def test_parse_file_marks_a_broken_docx_as_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.docx").write_bytes(b"this is not a zip file")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "failed"


DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
VARIANT_CONTENT_TYPES = {
    ".docm": "application/vnd.ms-word.document.macroEnabled.main+xml",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    ".dotm": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
}


def make_word_variant(path):
    """Build the test .docx, then save it as `path` with the label of its extension
    (.docm/.dotx/.dotm) in [Content_Types].xml - which is all Word changes."""
    base = path.parent / "base.docx"
    make_docx(base)
    content_type = VARIANT_CONTENT_TYPES[path.suffix]
    with zipfile.ZipFile(base) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(DOCX_CONTENT_TYPE.encode(), content_type.encode())
            dst.writestr(item, data)
    base.unlink()
    (path.parent / "img.png").unlink()


@pytest.mark.parametrize("ext", [".docm", ".dotx", ".dotm"])
def test_read_docx_rejects_word_variants(tmp_path, ext):
    f = tmp_path / f"doc{ext}"
    make_word_variant(f)

    with pytest.raises(ValueError, match="not a Word file"):
        read_docx(f)


@pytest.mark.parametrize("ext", [".docm", ".dotx", ".dotm"])
def test_read_word_variants_gives_same_text_as_docx(tmp_path, ext):
    f = tmp_path / f"doc{ext}"
    make_word_variant(f)

    assert read_word_variants(f) == EXPECTED_DOCX


def test_read_word_variants_leaves_the_file_on_disk_unchanged(tmp_path):
    f = tmp_path / "doc.docm"
    make_word_variant(f)
    before = f.read_bytes()

    read_word_variants(f)

    assert f.read_bytes() == before


def test_read_word_variants_also_reads_a_plain_docx(tmp_path):
    f = tmp_path / "doc.docx"
    make_docx(f)

    assert read_word_variants(f) == EXPECTED_DOCX


@pytest.mark.parametrize("ext", [".docm", ".dotx", ".dotm"])
def test_parse_file_reads_word_variants(tmp_path, ext):
    root = tmp_path / "corpus"
    root.mkdir()
    make_word_variant(root / f"doc{ext}")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_DOCX


def test_parse_file_marks_a_broken_docm_as_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.docm").write_bytes(b"this is not a zip file")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "failed"


@pytest.mark.parametrize(
    "ext, reader",
    [
        (".markdown", read_text),
        (".docx", read_docx),
        (".docm", read_word_variants),
        (".dotx", read_word_variants),
        (".dotm", read_word_variants),
        (".pptx", read_pptx),
    ],
)
def test_extension_is_registered(ext, reader):
    assert PARSERS.get(ext) is reader


def test_parse_file_reads_markdown_extension(tmp_path):
    docs = parse_all(tmp_path, {"notes.markdown": "# Σημειώσεις\n\nΚείμενο.".encode()})

    assert docs["notes.markdown"].parse_status == "ok"
    assert docs["notes.markdown"].content_type == "prose"
    assert docs["notes.markdown"].raw_text == "# Σημειώσεις\n\nΚείμενο."


LAYOUT_TITLE_AND_CONTENT = 1  
LAYOUT_TITLE_ONLY = 5          
LAYOUT_BLANK = 6               


def set_alt_text(shape, text):
    shape._element.xpath("./*/p:cNvPr")[0].set("descr", text)


def set_notes(slide, text):
    frame = slide.notes_slide.notes_text_frame
    assert frame is not None
    frame.text = text


def make_pptx(path):
    """A presentation with every case read_pptx has to handle."""
    image = path.parent / "img.png"
    Image.new("RGB", (60, 30), "white").save(image)
    inch = PptxInches(1)

    presentation = pptx.Presentation()

    slide = presentation.slides.add_slide(presentation.slide_layouts[LAYOUT_TITLE_AND_CONTENT])
    title = slide.shapes.title
    assert title is not None
    title.text = "  Εισαγωγή "
    body = slide.placeholders[1]
    assert isinstance(body, PptxShape)
    body.text_frame.text = "Πρώτη κουκκίδα"
    body.text_frame.add_paragraph().text = "Δεύτερη κουκκίδα"
    slide.shapes.add_textbox(inch, PptxInches(5), inch, inch)         
    set_notes(slide, "να πω για το dataset")

    slide = presentation.slides.add_slide(presentation.slide_layouts[LAYOUT_BLANK])
    table = slide.shapes.add_table(2, 2, inch, inch, PptxInches(4), inch).table
    table.cell(0, 0).text, table.cell(0, 1).text = "Όνομα", "Βαθμός"
    table.cell(1, 0).text, table.cell(1, 1).text = "Μαρία", "9"
    set_alt_text(slide.shapes.add_picture(str(image), inch, PptxInches(3)), "Διάγραμμα")
    set_alt_text(slide.shapes.add_picture(str(image), PptxInches(3), PptxInches(3)), "")
    outer = slide.shapes.add_group_shape()
    box = outer.shapes.add_textbox(PptxInches(5), PptxInches(5), inch, inch)
    box.text_frame.text = "Κείμενο μέσα σε ομάδα"
    inner = outer.shapes.add_group_shape()
    box = inner.shapes.add_textbox(PptxInches(6), PptxInches(6), inch, inch)
    box.text_frame.text = "Ομάδα μέσα σε ομάδα"

    slide = presentation.slides.add_slide(presentation.slide_layouts[LAYOUT_TITLE_ONLY])
    set_notes(slide, "   ")

    presentation.save(str(path))


EXPECTED_PPTX = (
    "# Slide 1: Εισαγωγή\n"
    "\n"
    "Πρώτη κουκκίδα\nΔεύτερη κουκκίδα\n"
    "\n"
    "Notes: να πω για το dataset\n"
    "\n"
    "# Slide 2\n"
    "\n"
    "Όνομα: Μαρία; Βαθμός: 9\n"
    "\n"
    "[Image: Διάγραμμα]\n"
    "\n"
    "Κείμενο μέσα σε ομάδα\n"
    "\n"
    "Ομάδα μέσα σε ομάδα\n"
    "\n"
    "# Slide 3"
)


def test_read_pptx_slides_titles_tables_images_groups_notes(tmp_path):
    f = tmp_path / "deck.pptx"
    make_pptx(f)

    assert read_pptx(f) == EXPECTED_PPTX


def test_read_pptx_does_not_repeat_the_title(tmp_path):
    f = tmp_path / "deck.pptx"
    make_pptx(f)

    assert read_pptx(f).count("Εισαγωγή") == 1


def test_read_pptx_empty_presentation(tmp_path):
    f = tmp_path / "empty.pptx"
    pptx.Presentation().save(str(f))

    assert read_pptx(f) == ""


@pytest.mark.parametrize("alt, expected", [("", []), ("Χάρτης", ["[Image: Χάρτης]"])])
def test_format_shape_picture(tmp_path, alt, expected):
    image = tmp_path / "img.png"
    Image.new("RGB", (60, 30), "white").save(image)
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[LAYOUT_BLANK])
    picture = slide.shapes.add_picture(str(image), PptxInches(0), PptxInches(0))
    set_alt_text(picture, alt)

    assert format_shape(picture) == expected


def test_parse_file_reads_pptx(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    make_pptx(root / "deck.pptx")
    (root / "img.png").unlink()  

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_PPTX


def test_parse_file_marks_a_broken_pptx_as_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.pptx").write_bytes(b"this is not a zip file")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "failed"
