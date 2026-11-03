from pathlib import Path

import pytest

from rag_fs.ingest.quality import quality_reasons
from rag_fs.models import Document

CONTROL = chr(0)


# the reasons of a document with this text, status and type
def reasons(text, status="ok", content_type="prose"):
    doc = Document(file_path=Path("notes.txt"), root_id="r", ext=".txt", raw_text=text, last_modified=0.0,
                   content_type=content_type, parse_status=status, sha256="0")
    return quality_reasons(doc)


# text of `chars` non-space characters, `control` of them control characters, and `spaces` spaces among them
def with_control(control, chars=100, spaces=0):
    return CONTROL * control + " " * spaces + "α" * (chars - control)


# text of 100 words, `mixed` of them with greek and latin letters together
def with_mixed(mixed, word="τηςclass"):
    return " ".join([word] * mixed + ["λέξη"] * (100 - mixed))


# (text, status, reasons)
QUALITY_CASES = {
    "clean greek text": ("Το κείμενο του μαθήματος είναι σωστό.", "ok", []),
    "empty text": ("", "empty", []),
    "text from ocr": ("Το κείμενο του μαθήματος είναι σωστό.", "ocr", ["ocr"]),
    "control characters above a tenth": (with_control(11), "ok", ["control_chars"]),
    "control characters at a tenth exactly": (with_control(10), "ok", []),
    "latex with a few control characters": (with_control(2), "ok", []),
    "spaces do not count": (with_control(11, spaces=60), "ok", ["control_chars"]),
    "new lines and tabs are not control characters": ("α\n\tβ\n\tγ\n\tδ", "ok", []),
    "one lost character": ("Το κείμενο " * 1000 + "\ufffd", "ok", ["replacement_chars"]),
    "mixed words above one in a hundred": (with_mixed(2), "ok", ["mixed_words"]),
    "mixed words at one in a hundred exactly": (with_mixed(1), "ok", []),
    "latin then greek is mixed too": (with_mixed(2, "classτης"), "ok", ["mixed_words"]),
    "accented greek letters count as greek": (with_mixed(2, "ήτανclass"), "ok", ["mixed_words"]),
    "polytonic greek letters count as greek": (with_mixed(2, "οἷςclass"), "ok", ["mixed_words"]),
    "one greek letter in a formula is not mixed": (with_mixed(5, "rsinθ"), "ok", []),
    "one latin letter is not mixed": (with_mixed(5, "xάρτιο"), "ok", []),
    "course codes with numbers are not mixed": (with_mixed(5, "ΗΥ100"), "ok", []),
    "all reasons, in this order": (
        CONTROL * 100 + " \ufffd " + with_mixed(5), "ocr", ["ocr", "control_chars", "replacement_chars", "mixed_words"]),
}


@pytest.mark.parametrize("text, status, expected", QUALITY_CASES.values(), ids=QUALITY_CASES.keys())
def test_quality_reasons(text, status, expected):
    assert reasons(text, status) == expected


# code and tables get the same checks
@pytest.mark.parametrize("content_type", ["code", "tabular"])
def test_quality_reasons_of_code_and_tables(content_type):
    assert reasons(with_control(11), content_type=content_type) == ["control_chars"]
