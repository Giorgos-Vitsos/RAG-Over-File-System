import datetime
import logging
from pathlib import Path

import pytest

from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.parsers import parse_file
from rag_fs.ingest.parsers.tabular import cell_to_str, format_rows
from rag_fs.ingest.parsers.text import read_text

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


# ---------- tabular: cell_to_str / format_rows ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, ""),
        (datetime.datetime(2024, 3, 1), "2024-03-01"),
        (datetime.datetime(2024, 3, 1, 14, 30), "2024-03-01 14:30:00"),
        (54, "54"),
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
    # Same layout as a real course spreadsheet: empty first row and column,
    # a title, a row of column groups, then the real header.
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
