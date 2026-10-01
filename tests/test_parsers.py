import pytest

from rag_fs.ingest.parsers.text import read_text


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
