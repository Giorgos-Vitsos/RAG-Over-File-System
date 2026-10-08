import datetime
import logging
import os
import zipfile
from pathlib import Path

import docx
import openpyxl
import pandas as pd
import pptx
import pytest
from docx.oxml import parse_xml
from docx.shared import Inches
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import CoveredTableCell, Table as OdfTable, TableCell, TableRow
from odf.text import P as OdfP
from PIL import Image
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.shapes.autoshape import Shape as PptxShape
from pptx.util import Inches as PptxInches

from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.parsers import PARSERS, parse_file
from rag_fs.ingest.parsers.office import (
    format_shape, heading_level, read_docx, read_pptx, read_rtf, read_slide_variants,
    read_word_variants,
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
        (".pptm", read_pptx),
        (".potx", read_slide_variants),
        (".potm", read_slide_variants),
        (".ppsx", read_slide_variants),
        (".ppsm", read_slide_variants),
        (".rtf", read_rtf),
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


# ---------- office: PowerPoint variants (.pptm, .potx, .potm, .ppsx, .ppsm) ----------

# Written out here on purpose (not imported from office.py),
# so a typo in the constants of office.py is caught.
PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
SLIDE_VARIANT_CONTENT_TYPES = {
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    ".potm": "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
    ".ppsm": "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml",
}
SLIDE_VARIANTS_PPTX_REFUSES = [".potx", ".potm", ".ppsx", ".ppsm"]


def make_slide_variant(path):
    """Build the test .pptx, then save it as `path` with the label of its extension."""
    base = path.parent / "base.pptx"
    make_pptx(base)
    content_type = SLIDE_VARIANT_CONTENT_TYPES[path.suffix]
    with zipfile.ZipFile(base) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(PPTX_CONTENT_TYPE.encode(), content_type.encode())
            dst.writestr(item, data)
    base.unlink()
    (path.parent / "img.png").unlink()


def test_read_pptx_accepts_pptm(tmp_path):
    # Unlike python-docx with .docm, python-pptx accepts the macro-enabled label.
    f = tmp_path / "deck.pptm"
    make_slide_variant(f)

    assert read_pptx(f) == EXPECTED_PPTX


@pytest.mark.parametrize("ext", SLIDE_VARIANTS_PPTX_REFUSES)
def test_read_pptx_rejects_slide_variants(tmp_path, ext):
    # The reason read_slide_variants exists.
    f = tmp_path / f"deck{ext}"
    make_slide_variant(f)

    with pytest.raises(ValueError, match="not a PowerPoint file"):
        read_pptx(f)


@pytest.mark.parametrize("ext", SLIDE_VARIANTS_PPTX_REFUSES)
def test_read_slide_variants_gives_same_text_as_pptx(tmp_path, ext):
    f = tmp_path / f"deck{ext}"
    make_slide_variant(f)

    assert read_slide_variants(f) == EXPECTED_PPTX


def test_read_slide_variants_leaves_the_file_on_disk_unchanged(tmp_path):
    f = tmp_path / "deck.ppsx"
    make_slide_variant(f)
    before = f.read_bytes()

    read_slide_variants(f)

    assert f.read_bytes() == before


@pytest.mark.parametrize("ext", list(SLIDE_VARIANT_CONTENT_TYPES))
def test_parse_file_reads_slide_variants(tmp_path, ext):
    root = tmp_path / "corpus"
    root.mkdir()
    make_slide_variant(root / f"deck{ext}")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_PPTX


def test_parse_file_marks_a_broken_potx_as_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.potx").write_bytes(b"this is not a zip file")

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "failed"


RTF_GREEK_CP1253 = (
    "{\\rtf1\\ansi\\ansicpg1253\\deff0{\\fonttbl{\\f0 Arial;}}\r\n"
    "\\f0 \\'c3\\'e5\\'e9\\'e1 \\'f3\\'ef\\'f5\\par\r\n"
    "\\par\r\n"
    "Second paragraph\\line same paragraph, new line\\par\r\n"
    "}"
)
EXPECTED_RTF = "Γεια σου\n\nSecond paragraph\nsame paragraph, new line"


def write_rtf(tmp_path, content, name="doc.rtf", encoding="ascii"):
    f = tmp_path / name
    f.write_bytes(content.encode(encoding))
    return f


def test_read_rtf_greek_cp1253_escapes(tmp_path):
    assert read_rtf(write_rtf(tmp_path, RTF_GREEK_CP1253)) == EXPECTED_RTF


def test_read_rtf_greek_unicode_escapes(tmp_path):
    rtf = r"{\rtf1\ansi\ansicpg1252\uc1 \u915?\u949?\u953?\u945? \u963?\u959?\u965?\par}"

    assert read_rtf(write_rtf(tmp_path, rtf)) == "Γεια σου"


def test_read_rtf_greek_written_directly_in_cp1253(tmp_path):
    rtf = "{\\rtf1\\ansi\\ansicpg1253 Γεια σου κόσμε\\par}"

    assert read_rtf(write_rtf(tmp_path, rtf, encoding="cp1253")) == "Γεια σου κόσμε"


def test_read_rtf_strips_blank_lines_and_spaces_at_both_ends(tmp_path):
    rtf = "\r\n  {\\rtf1\\ansi \\par\\par Text\\par\\par\\par}"

    assert read_rtf(write_rtf(tmp_path, rtf)) == "Text"


def test_read_rtf_empty_document(tmp_path):
    assert read_rtf(write_rtf(tmp_path, r"{\rtf1\ansi}")) == ""


def test_read_rtf_drops_pictures(tmp_path):
    rtf = r"{\rtf1\ansi Before {\pict\pngblip\picw10\pich10 89504e470d0a1a0a0000}after\par}"

    assert read_rtf(write_rtf(tmp_path, rtf)) == "Before after"


def test_read_rtf_tables_stay_as_cells_with_bars(tmp_path):
    rtf = (r"{\rtf1\ansi\trowd\cellx2000\cellx4000 Name\cell Grade\cell\row"
           r"\trowd\cellx2000\cellx4000 Maria\cell 9\cell\row\pard After table\par}")

    assert read_rtf(write_rtf(tmp_path, rtf)) == "Name|Grade|\nMaria|9|\nAfter table"


@pytest.mark.parametrize("content", ["this is not an rtf file", "", "{\\rtfX not really}"])
def test_read_rtf_rejects_files_that_are_not_rtf(tmp_path, content):
    with pytest.raises(ValueError):
        read_rtf(write_rtf(tmp_path, content))


def test_parse_file_reads_rtf(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    write_rtf(root, RTF_GREEK_CP1253)

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_RTF


@pytest.mark.parametrize(
    "content, status",
    [
        ("this is not an rtf file", "failed"),   
        (r"{\rtf1\ansi}", "empty"),
        ("", "failed"),                         
    ],
)
def test_parse_file_rtf_status(tmp_path, content, status):
    root = tmp_path / "corpus"
    root.mkdir()
    write_rtf(root, content)

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.parse_status == status

@pytest.mark.parametrize("name, data", [
    ("notes.txt", "Γεια".encode()),       
    ("broken.docx", b"not a zip"),         
    ("song.mp3", b"\x00\x01"),           
])
def test_parse_file_keeps_the_last_modified_time(tmp_path, name, data):
    root = tmp_path / "corpus"
    root.mkdir()
    f = root / name
    f.write_bytes(data)
    when = datetime.datetime(2024, 3, 1, 14, 30).timestamp()
    os.utime(f, (when, when))  

    doc = parse_file(scan(CorpusConfig(roots=[root])).files[0])

    assert doc.last_modified == when

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def test_parse_file_reads_upper_case_extensions(tmp_path):
    f = tmp_path / "base.docx"
    d = docx.Document()
    d.add_paragraph("Αναφορά")
    d.save(str(f))

    doc = parse_all(tmp_path, {"REPORT.DOCX": f.read_bytes()})["REPORT.DOCX"]

    assert doc.parse_status == "ok"
    assert doc.raw_text == "Αναφορά"


def test_zero_byte_text_file_is_empty(tmp_path):
    assert parse_all(tmp_path, {"empty.txt": b""})["empty.txt"].parse_status == "empty"


@pytest.mark.parametrize("word", [
    pytest.param("Ναι", marks=pytest.mark.xfail(strict=True, reason="L28: too short to detect cp1253")),
    "Όχι", "Άρτα", "Χανιά", "Ηράκλειο",
])
def test_read_text_short_greek_words_in_cp1253(tmp_path, word):
    f = tmp_path / "short.txt"
    f.write_bytes(word.encode("cp1253"))

    assert read_text(f) == word


@pytest.mark.xfail(strict=True, reason="L28: read as cp1253, so Ά becomes ¶")
def test_read_text_iso_8859_7_capital_with_accent(tmp_path):
    text = "Άνοιξη στην Ήπειρο, Ώρα για Ύδρα."
    f = tmp_path / "iso.txt"
    f.write_bytes(text.encode("iso8859_7"))

    assert read_text(f) == text


@pytest.mark.xfail(strict=True, reason="L28: one bad byte makes the whole file fail")
def test_read_text_utf8_with_stray_windows_quotes_keeps_the_greek(tmp_path):
    f = tmp_path / "mixed.txt"
    f.write_bytes("Σημειώσεις για την εξεταστική ".encode() + b"\x93quote\x94"
                  + " και τα θέματα του Ιουνίου.".encode())

    text = read_text(f)

    assert "Σημειώσεις για την εξεταστική" in text
    assert "θέματα του Ιουνίου" in text


def test_read_csv_excel_utf8_bom_is_not_part_of_the_first_header(tmp_path):
    f = tmp_path / "excel.csv"
    f.write_bytes("Όνομα;Βαθμός\r\nΜαρία;9\r\n".encode("utf-8-sig"))

    assert read_csv(f) == "Όνομα: Μαρία; Βαθμός: 9"


@pytest.mark.parametrize("break_in_cell", [
    "\n",   
    pytest.param("\r\n", marks=pytest.mark.xfail(strict=True, reason="L29: \\r stays in the cell")),
])
def test_read_csv_line_break_inside_quotes(tmp_path, break_in_cell):
    f = tmp_path / "notes.csv"
    f.write_bytes(f'name,comment\r\nmaria,"first line{break_in_cell}second line"\r\n'.encode())

    assert read_csv(f) == "name: maria; comment: first line / second line"


@pytest.mark.xfail(strict=True, reason="L29: fix plan step 1")
@pytest.mark.parametrize("line_break", ["\r\n", "\r"])   
def test_cell_to_str_any_line_break_becomes_a_slash(line_break):
    assert cell_to_str(f"first line{line_break}second line") == "first line / second line"


@pytest.mark.xfail(strict=True, reason="L29: fix plan step 1")
def test_read_xlsx_cell_with_windows_line_break(tmp_path):
    f = tmp_path / "notes.xlsx"
    make_xlsx(f, [("Σημειώσεις", [["Όνομα", "Σχόλιο"], ["Μαρία", "πρώτη γραμμή\r\nδεύτερη γραμμή"]])])

    assert read_xlsx(f) == "# Sheet: Σημειώσεις\nΌνομα: Μαρία; Σχόλιο: πρώτη γραμμή / δεύτερη γραμμή"


@pytest.mark.xfail(strict=True, reason="L29: fix plan step 1")
def test_format_rows_keeps_values_beyond_the_header_far_down_the_table():
    """The wider row is past the first 10 rows, so the header itself is found correctly."""
    rows = [
        ("Όνομα", "Βαθμός"),
        *[(f"Φοιτητής {i}", "8") for i in range(1, 11)],
        ("Μαρία", "9", "άριστα"),
    ]

    assert format_rows(rows).splitlines()[-1] == "Όνομα: Μαρία; Βαθμός: 9; col3: άριστα"


def test_read_csv_semicolon_file_with_commas_inside_quotes(tmp_path):
    f = tmp_path / "bills.csv"
    f.write_bytes(
        "Περιγραφή;Ποσό\n\"Ρεύμα, νερό, τηλέφωνο\";54\n\"Ενοίκιο, κοινόχρηστα\";400\n".encode()
    )

    assert read_csv(f) == (
        "Περιγραφή: Ρεύμα, νερό, τηλέφωνο; Ποσό: 54\n"
        "Περιγραφή: Ενοίκιο, κοινόχρηστα; Ποσό: 400"
    )


@pytest.mark.xfail(strict=True, reason="L29: a full data row wins over the header")
def test_read_csv_pandas_index_column_has_no_header(tmp_path):
    """df.to_csv() writes the index as a first column with an empty header cell."""
    f = tmp_path / "pandas.csv"
    f.write_bytes(b",city,population\n0,Heraklion,180000\n1,Chania,110000\n")

    assert read_csv(f) == (
        "col1: 0; city: Heraklion; population: 180000\n"
        "col1: 1; city: Chania; population: 110000"
    )


@pytest.mark.xfail(strict=True, reason="L29: a wider data row wins over the header")
def test_read_csv_values_beyond_the_last_header_are_kept(tmp_path):
    f = tmp_path / "ragged.csv"
    f.write_bytes(b"name,grade\nmaria,9,excellent\n")

    assert read_csv(f) == "name: maria; grade: 9; col3: excellent"


GRADES = [("Όνομα", "Γραπτό", "Προφορικό"), ("Μαρία", "9", "8"), ("Νίκος", "απαλλαγή", "")]
EXPECTED_MERGED = (
    "Όνομα: Μαρία; Γραπτό: 9; Προφορικό: 8\n"
    "Όνομα: Νίκος; Γραπτό: απαλλαγή; Προφορικό: απαλλαγή"
)


def test_read_docx_merged_cells(tmp_path):
    d = docx.Document()
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate(GRADES):
        for c, value in enumerate(row):
            t.cell(r, c).text = value
    t.cell(2, 1).merge(t.cell(2, 2)).text = "απαλλαγή"
    f = tmp_path / "merged.docx"
    d.save(str(f))

    assert read_docx(f) == EXPECTED_MERGED


@pytest.mark.xfail(strict=True, reason="L30: the covered cell is read as empty")
def test_read_pptx_merged_cells(tmp_path):
    p = pptx.Presentation()
    slide = p.slides.add_slide(p.slide_layouts[LAYOUT_BLANK])
    t = slide.shapes.add_table(3, 3, PptxInches(1), PptxInches(1), PptxInches(6), PptxInches(2)).table
    for r, row in enumerate(GRADES):
        for c, value in enumerate(row):
            t.cell(r, c).text = value
    t.cell(2, 1).merge(t.cell(2, 2))
    f = tmp_path / "merged.pptx"
    p.save(str(f))

    assert read_pptx(f) == "# Slide 1\n\n" + EXPECTED_MERGED


@pytest.mark.xfail(strict=True, reason="L30: the covered cell is read as empty")
def test_read_xlsx_merged_cells_across(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Βαθμοί"
    for row in GRADES:
        sheet.append(row)
    sheet.merge_cells("B3:C3")
    f = tmp_path / "merged.xlsx"
    book.save(f)

    assert read_xlsx(f) == "# Sheet: Βαθμοί\n" + EXPECTED_MERGED


@pytest.mark.xfail(strict=True, reason="L30: the covered cell is read as empty")
def test_read_xlsx_merged_cells_down(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Έξοδα"
    for row in [("Κατηγορία", "Μήνας", "Ποσό"), ("Ρεύμα", "Μάρτιος", 54), (None, "Απρίλιος", 60)]:
        sheet.append(row)
    sheet.merge_cells("A2:A3")
    f = tmp_path / "down.xlsx"
    book.save(f)

    assert read_xlsx(f) == (
        "# Sheet: Έξοδα\n"
        "Κατηγορία: Ρεύμα; Μήνας: Μάρτιος; Ποσό: 54\n"
        "Κατηγορία: Ρεύμα; Μήνας: Απρίλιος; Ποσό: 60"
    )


@pytest.mark.xfail(strict=True, reason="L30: the covered cell is read as empty")
def test_read_ods_merged_cells(tmp_path):
    """Built like LibreOffice saves it: a spanning cell, then a covered-table-cell."""
    doc = OpenDocumentSpreadsheet()
    table = OdfTable(name="Βαθμοί")
    for row in GRADES:
        tr = TableRow()
        for c, value in enumerate(row):
            if row[0] == "Νίκος" and c == 2:
                tr.addElement(CoveredTableCell())
                continue
            span = 2 if row[0] == "Νίκος" and c == 1 else 1
            cell = TableCell(valuetype="string", numbercolumnsspanned=span)
            cell.addElement(OdfP(text=value))
            tr.addElement(cell)
        table.addElement(tr)
    doc.spreadsheet.addElement(table)  # pyright: ignore[reportAttributeAccessIssue] (odfpy adds it at runtime)
    f = tmp_path / "merged.ods"
    doc.save(str(f))

    assert read_ods(f) == "# Sheet: Βαθμοί\n" + EXPECTED_MERGED


def test_read_xlsx_formula_uses_the_value_excel_saved(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Τιμές"
    sheet.append(["Τεμάχια", "Τιμή", "Σύνολο"])
    sheet.append([2, 5, "=A2*B2"])
    plain = tmp_path / "plain.xlsx"
    book.save(plain)
    f = tmp_path / "formula.xlsx"
    with zipfile.ZipFile(plain) as src, zipfile.ZipFile(f, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                assert b"<f>A2*B2</f><v></v>" in data
                data = data.replace(b"<f>A2*B2</f><v></v>", b"<f>A2*B2</f><v>10</v>")
            dst.writestr(item, data)

    assert read_xlsx(f) == "# Sheet: Τιμές\nΤεμάχια: 2; Τιμή: 5; Σύνολο: 10"


@pytest.mark.xfail(strict=True, reason="L31: ods dates come out with 00:00:00")
def test_read_ods_real_date_cells(tmp_path):
    f = tmp_path / "dates.ods"
    make_ods(f, [("Έξοδα", [["Ημερομηνία", "Ποσό"], [datetime.datetime(2024, 3, 1), 54]])])

    assert read_ods(f) == "# Sheet: Έξοδα\nΗμερομηνία: 2024-03-01; Ποσό: 54"


@pytest.mark.xfail(strict=True, reason="L32: cell.text skips tables inside the cell")
def test_read_docx_table_inside_a_table_cell(tmp_path):
    d = docx.Document()
    outer = d.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = "Στοιχεία"
    inner = outer.cell(0, 1).add_table(rows=2, cols=2)
    inner.cell(0, 0).text, inner.cell(0, 1).text = "Τηλέφωνο", "Email"
    inner.cell(1, 0).text, inner.cell(1, 1).text = "2810123456", "maria@uoc.gr"
    f = tmp_path / "nested.docx"
    d.save(str(f))

    text = read_docx(f)

    assert "2810123456" in text
    assert "maria@uoc.gr" in text


@pytest.mark.xfail(strict=True, reason="L32: text boxes are not read")
def test_read_docx_text_box_appears_once(tmp_path):
    """Word saves a text box twice: as a modern shape AND as an old VML copy."""
    d = docx.Document()
    d.add_paragraph("Πριν")
    box = "<w:txbxContent><w:p><w:r><w:t>Κείμενο σε πλαίσιο</w:t></w:r></w:p></w:txbxContent>"
    d.add_paragraph()._p.append(parse_xml(
        f'<w:r xmlns:w="{W_NS}"'
        ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        ' xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
        ' xmlns:v="urn:schemas-microsoft-com:vml">'
        '<mc:AlternateContent>'
        '<mc:Choice Requires="wps"><w:drawing><wp:anchor><wp:docPr id="5" name="Text Box 5"/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        f'<wps:wsp><wps:txbx>{box}</wps:txbx><wps:bodyPr/></wps:wsp>'
        '</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:shape><v:textbox>{box}</v:textbox></v:shape></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r>'
    ))
    d.add_paragraph("Μετά")
    f = tmp_path / "textbox.docx"
    d.save(str(f))

    text = read_docx(f)

    assert text.count("Κείμενο σε πλαίσιο") == 1
    assert text.index("Πριν") < text.index("Κείμενο σε πλαίσιο") < text.index("Μετά")


@pytest.mark.xfail(strict=True, reason="L32: content controls (w:sdt) are skipped")
def test_read_docx_content_control_around_paragraphs(tmp_path):
    d = docx.Document()
    d.add_paragraph("Αίτηση")
    d.paragraphs[-1]._p.addnext(parse_xml(
        f'<w:sdt xmlns:w="{W_NS}"><w:sdtPr/><w:sdtContent>'
        '<w:p><w:r><w:t>Όνομα φοιτητή: Μαρία Παπαδάκη</w:t></w:r></w:p>'
        '</w:sdtContent></w:sdt>'
    ))
    f = tmp_path / "form.docx"
    d.save(str(f))

    assert read_docx(f) == "Αίτηση\n\nΌνομα φοιτητή: Μαρία Παπαδάκη"


@pytest.mark.xfail(strict=True, reason="L32: content controls (w:sdt) are skipped")
def test_read_docx_content_control_inside_a_paragraph(tmp_path):
    d = docx.Document()
    d.add_paragraph("Ημερομηνία: ")._p.append(parse_xml(
        f'<w:sdt xmlns:w="{W_NS}"><w:sdtPr/><w:sdtContent>'
        '<w:r><w:t>15/06/2026</w:t></w:r></w:sdtContent></w:sdt>'
    ))
    f = tmp_path / "inline_form.docx"
    d.save(str(f))

    assert read_docx(f) == "Ημερομηνία: 15/06/2026"


@pytest.mark.xfail(strict=True, reason="L32: inserted text (w:ins) is skipped")
def test_read_docx_tracked_changes_give_the_current_text(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("Η προθεσμία είναι ")
    p._p.append(parse_xml(
        f'<w:del xmlns:w="{W_NS}" w:id="1" w:author="A" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:delText>η Δευτέρα</w:delText></w:r></w:del>'
    ))
    p._p.append(parse_xml(
        f'<w:ins xmlns:w="{W_NS}" w:id="2" w:author="A" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t>η Τρίτη</w:t></w:r></w:ins>'
    ))
    p.add_run(".")
    f = tmp_path / "tracked.docx"
    d.save(str(f))

    assert read_docx(f) == "Η προθεσμία είναι η Τρίτη."


def test_read_docx_hyperlink_text(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("Δες ")
    p._p.append(parse_xml(
        f'<w:hyperlink xmlns:w="{W_NS}" w:anchor="top"><w:r><w:t>τον οδηγό</w:t></w:r></w:hyperlink>'
    ))
    p.add_run(" για λεπτομέρειες.")
    f = tmp_path / "link.docx"
    d.save(str(f))

    assert read_docx(f) == "Δες τον οδηγό για λεπτομέρειες."


@pytest.mark.xfail(strict=True, reason="L33: charts are not read")
def test_read_pptx_chart_title(tmp_path):
    p = pptx.Presentation()
    slide = p.slides.add_slide(p.slide_layouts[LAYOUT_BLANK])
    data = CategoryChartData()
    data.categories = ["2023", "2024"]
    data.add_series("Φοιτητές", (120, 150))
    frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, PptxInches(1), PptxInches(1),
                                   PptxInches(6), PptxInches(4), data)  # pyright: ignore[reportArgumentType]
    chart = frame.chart  # pyright: ignore[reportAttributeAccessIssue]
    chart.has_title = True
    chart.chart_title.text_frame.text = "Εγγραφές ανά έτος"
    f = tmp_path / "chart.pptx"
    p.save(str(f))

    assert "Εγγραφές ανά έτος" in read_pptx(f)


def test_read_rtf_document_properties_are_not_body_text(tmp_path):
    f = tmp_path / "info.rtf"
    f.write_bytes(
        rb"{\rtf1\ansi\ansicpg1253{\fonttbl{\f0 Arial;}}"
        rb"{\info{\title Secret title}{\author Some Author}}"
        rb"\f0 Body text.\par}"
    )

    assert read_rtf(f) == "Body text."


def test_read_rtf_footnote_is_not_glued_into_the_sentence(tmp_path):
    f = tmp_path / "notes.rtf"
    f.write_bytes(
        rb"{\rtf1\ansi{\header Page header}"
        rb"Main sentence{\super\chftn{\footnote\pard\plain\chftn Footnote text.}} continues.\par}"
    )

    assert "Main sentence continues." in read_rtf(f)
