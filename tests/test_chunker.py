import re
import re
from pathlib import Path, PureWindowsPath

import pytest

from rag_fs.config import ChunkingConfig
from rag_fs.ingest import chunker
from rag_fs.ingest.chunker import (
    chunk_doc, code_language, length_stats, make_chunk, split_blocks, split_code, split_rows, split_sections,
    split_windows, use_grammars,
)
from rag_fs.models import Chunk, Document


def sections(text):
    return [(text[start:end], title) for start, end, title in split_sections(text)]


CHAPTER = "# Κεφάλαιο 2\nκ\n## 2.1 Εισαγωγή\nε\n## 2.2 Μέθοδος\nμ"
FENCE = "```"

# (text, [(text of the section, its titles)])
SECTION_CASES = {
    "no headings is one section": ("Κείμενο.\nΚαι άλλο.", [("Κείμενο.\nΚαι άλλο.", "")]),
    "empty text has no sections": ("", []),
    "only spaces has no sections": ("  \n\n \t", []),
    "headings split the text": ("# Α\nα\n\n# Β\nβ", [("# Α\nα", "Α"), ("# Β\nβ", "Β")]),
    "text before the first heading": ("Πρόλογος\n\n# Α\nα", [("Πρόλογος", ""), ("# Α\nα", "Α")]),
    "spaces before the first heading are not a section": ("\n\n  \n# Α\nα", [("# Α\nα", "Α")]),
    "spaces around a section are left out": ("# Α\nα  \n\n\n# Β\nβ\n\n", [("# Α\nα", "Α"), ("# Β\nβ", "Β")]),
    "spaces at the start of the text are left out": ("\n  Κείμενο\n# Α\nα", [("Κείμενο", ""), ("# Α\nα", "Α")]),
    "path of titles": (CHAPTER, [("# Κεφάλαιο 2\nκ", "Κεφάλαιο 2"),
                                 ("## 2.1 Εισαγωγή\nε", "Κεφάλαιο 2 > 2.1 Εισαγωγή"),
                                 ("## 2.2 Μέθοδος\nμ", "Κεφάλαιο 2 > 2.2 Μέθοδος")]),
    "back to a higher level": ("# Α\n## Β\n### Γ\nγ\n# Δ\nδ",
                               [("# Α", "Α"), ("## Β", "Α > Β"), ("### Γ\nγ", "Α > Β > Γ"), ("# Δ\nδ", "Δ")]),
    "a level is skipped": ("# Α\n### Β\nβ\n## Γ\nγ", [("# Α", "Α"), ("### Β\nβ", "Α > Β"), ("## Γ\nγ", "Α > Γ")]),
    "starts below the top level": ("## Α\nα\n# Β\nβ\n## Γ\nγ", [("## Α\nα", "Α"), ("# Β\nβ", "Β"), ("## Γ\nγ", "Β > Γ")]),
    "heading with nothing under it is still a section": (
        "# Slide 1: Αρχή\n\n# Slide 2\n\nκείμενο", [("# Slide 1: Αρχή", "Slide 1: Αρχή"), ("# Slide 2\n\nκείμενο", "Slide 2")]),
    "sheets": ("# Sheet: Έξοδα\nΜήνας: Μάρτιος; Ποσό: 54\n\n# Sheet: Έσοδα\nΜήνας: Μάιος; Ποσό: 900",
               [("# Sheet: Έξοδα\nΜήνας: Μάρτιος; Ποσό: 54", "Sheet: Έξοδα"),
                ("# Sheet: Έσοδα\nΜήνας: Μάιος; Ποσό: 900", "Sheet: Έσοδα")]),
    "heading at the very end": ("α\n# Τέλος", [("α", ""), ("# Τέλος", "Τέλος")]),
    "spaces around the title": ("#   Τίτλος  \nα", [("#   Τίτλος  \nα", "Τίτλος")]),
    "six hashes is a heading": ("###### Βαθιά\nβ", [("###### Βαθιά\nβ", "Βαθιά")]),
    # not headings (L37 and others): the whole text stays one section without a title
    "seven hashes": ("α\n####### όχι", [("α\n####### όχι", "")]),
    "column named #": ("#: 1; Όνομα: Μαρία\n#: 2; Όνομα: Νίκος", [("#: 1; Όνομα: Μαρία\n#: 2; Όνομα: Νίκος", "")]),
    "hashtag": ("#ΗΥ252 σημειώσεις", [("#ΗΥ252 σημειώσεις", "")]),
    "hash in the middle of a line": ("Βαθμός # 1\nκαι C# κώδικας", [("Βαθμός # 1\nκαι C# κώδικας", "")]),
    "hash after spaces": ("α\n  # όχι", [("α\n  # όχι", "")]),
    "tab after the hash": ("α\n#\tόχι", [("α\n#\tόχι", "")]),
    "hash and space with no title": ("α\n# \nβ", [("α\n# \nβ", "")]),
    # only \n breaks a line: pdftotext puts \x0c between pages, and offsets must match the text (D10)
    "form feed is not a line break": ("α\x0c# όχι\nβ", [("α\x0c# όχι\nβ", "")]),
    # code between ``` lines (D105): what looks like a heading there is a comment
    "comment inside code": (f"# Α\n{FENCE}python\n# load data\nx = 1\n{FENCE}\nα",
                            [(f"# Α\n{FENCE}python\n# load data\nx = 1\n{FENCE}\nα", "Α")]),
    "heading after the code": (f"# Α\n{FENCE}\n# όχι\n{FENCE}\n## Β\nβ",
                               [(f"# Α\n{FENCE}\n# όχι\n{FENCE}", "Α"), ("## Β\nβ", "Α > Β")]),
    "two code blocks": (f"{FENCE}\n# x\n{FENCE}\n# Α\n{FENCE}py\n# y\n{FENCE}",
                        [(f"{FENCE}\n# x\n{FENCE}", ""), (f"# Α\n{FENCE}py\n# y\n{FENCE}", "Α")]),
    "code that is never closed": (f"# Α\n{FENCE}\n# όχι\n## ούτε αυτό", [(f"# Α\n{FENCE}\n# όχι\n## ούτε αυτό", "Α")]),
    "only a bare ``` closes the code": (f"{FENCE}\n# όχι\n{FENCE}python\n# ακόμα όχι\n{FENCE}\n# Α\nα",
                                        [(f"{FENCE}\n# όχι\n{FENCE}python\n# ακόμα όχι\n{FENCE}", ""), ("# Α\nα", "Α")]),
    "``` in the middle of a line is not code": (f"# Α\nγράψε {FENCE} εδώ\n# Β\nβ",
                                                [(f"# Α\nγράψε {FENCE} εδώ", "Α"), ("# Β\nβ", "Β")]),
}


@pytest.mark.parametrize("text, expected", SECTION_CASES.values(), ids=SECTION_CASES.keys())
def test_split_sections(text, expected):
    assert sections(text) == expected


LONG = ("Εισαγωγικό κείμενο.\n\n# Κεφάλαιο 1\n\nΠρώτη παράγραφος.\n\n## 1.1\n\n" + "λέξη " * 300
        + f"\n\n{FENCE}\n# σχόλιο\n{FENCE}\n\n#: 1; Όνομα: Μαρία\n\n### 1.1.1\n\nτέλος  \n\n\n# Κεφάλαιο 2\n")


# for every text: the sections are in order, do not overlap, have no spaces at their edges,
# and together cover every character that is not a space
@pytest.mark.parametrize("text", [LONG] + [text for text, _ in SECTION_CASES.values()])
def test_split_sections_covers_the_text(text):
    found = split_sections(text)
    covered = [False] * len(text)
    last_end = 0
    for start, end, _ in found:
        assert last_end <= start < end <= len(text)
        assert text[start:end] == text[start:end].strip()
        covered[start:end] = [True] * (end - start)
        last_end = end
    assert all(covered[i] for i, char in enumerate(text) if not char.isspace())


# K2: windows of words inside a section (D103: the size is counted in words)


def windows(text, words, overlap, start=0, end=None):
    if end is None:
        end = len(text)
    return [text[s:e] for s, e in split_windows(text, start, end, words, overlap)]


def numbered(count):
    return " ".join(f"w{i}" for i in range(count))


# (text, words, overlap, [text of each window])
WINDOW_CASES = {
    "empty text has no windows": ("", 5, 0.2, []),
    "only spaces has no windows": (" \n\t ", 5, 0.2, []),
    "fewer words than the size is one window": ("  α β γ\n", 5, 0.2, ["α β γ"]),
    "exactly the size is one window": ("α β γ δ ε", 5, 0.2, ["α β γ δ ε"]),
    "one word more makes a small last window": ("α β γ δ ε ζ", 5, 0.2, ["α β γ δ ε", "ε ζ"]),
    "no overlap": (numbered(10), 5, 0, ["w0 w1 w2 w3 w4", "w5 w6 w7 w8 w9"]),
    "half overlap": (numbered(8), 4, 0.5, ["w0 w1 w2 w3", "w2 w3 w4 w5", "w4 w5 w6 w7"]),
    "the last window has what is left": (numbered(9), 4, 0.5, ["w0 w1 w2 w3", "w2 w3 w4 w5", "w4 w5 w6 w7", "w6 w7 w8"]),
    "overlap below half a word is none": ("α β γ", 2, 0.2, ["α β", "γ"]),
    "window of one word": ("α β γ", 1, 0, ["α", "β", "γ"]),
    "spaces and new lines inside a window are kept": ("α  β\n\nγ\tδ", 2, 0, ["α  β", "γ\tδ"]),
    "punctuation belongs to its word": ("Γεια, σου! Τι κάνεις;", 2, 0, ["Γεια, σου!", "Τι κάνεις;"]),
    "no-break space separates words": ("α\xa0β γ", 2, 0, ["α\xa0β", "γ"]),
    "form feed separates words": ("α\x0cβ γ", 2, 0, ["α\x0cβ", "γ"]),
}


@pytest.mark.parametrize("text, words, overlap, expected", WINDOW_CASES.values(), ids=WINDOW_CASES.keys())
def test_split_windows(text, words, overlap, expected):
    assert windows(text, words, overlap) == expected


# offsets are positions in the whole text, not in the section
def test_split_windows_inside_a_range():
    text = "πριν α β γ δ μετά"
    start, end = text.index("α"), text.index(" μετά")
    assert windows(text, 3, 0, start, end) == ["α β γ", "δ"]


# 0.29 * 100 is 28.999... in floats: cutting the decimals would give 28
def test_split_windows_overlap_is_rounded():
    first, second = windows(numbered(200), 100, 0.29)[:2]
    assert len(set(first.split()) & set(second.split())) == 29


# settings that would give no window, gaps between windows, or a loop that never ends
@pytest.mark.parametrize("words, overlap", [(0, 0.2), (-5, 0.2), (5, -0.2), (5, 1), (5, 1.5), (5, 0.95)],
                         ids=["size 0", "negative size", "negative overlap", "overlap 1", "overlap above 1",
                              "overlap that rounds to the whole window"])
def test_split_windows_rejects_bad_settings(words, overlap):
    with pytest.raises(ValueError):
        windows("α β γ", words, overlap)


MIXED = " ".join(f"λέξη{i}," + ("\n\n" if i % 37 == 0 else "") for i in range(1000))


# for every text and setting: the windows are in order, start and end on a word, have the size
# (only the last may be smaller), share the overlap with the next one, and together hold every word
@pytest.mark.parametrize("text", [LONG, MIXED, numbered(401)], ids=["long", "mixed", "401 words"])
@pytest.mark.parametrize("words, overlap", [(400, 0.2), (5, 0.2), (3, 0.5), (1, 0), (7, 0)])
def test_split_windows_covers_the_words(text, words, overlap):
    found = split_windows(text, 0, len(text), words, overlap)
    shared = round(words * overlap)
    for i, (start, end) in enumerate(found):
        piece = text[start:end]
        assert piece == piece.strip() and piece
        assert start == 0 or text[start - 1].isspace()
        assert end == len(text) or text[end].isspace()
        count = len(piece.split())
        if i < len(found) - 1:
            assert count == words
        else:
            assert count <= words
        if i > 0:
            last_start, last_end = found[i - 1]
            assert last_start < start and last_end < end
            if start < last_end:
                assert len(text[start:last_end].split()) == shared
            else:
                assert shared == 0
                assert text[last_end:start].split() == []  # no word is left between two windows
    assert found[0][0] == len(text) - len(text.lstrip())  # starts at the first word
    assert found[-1][1] == len(text.rstrip())  # ends at the last word


# K3: chunks with their header (D109) and every document turned into chunks (D32, D104, D108)


def document(text, path="notes/os.md", content_type="prose", status="ok"):
    return Document(file_path=Path(path), root_id="root_0", ext=Path(path).suffix, raw_text=text,
                    last_modified=0.0, content_type=content_type, parse_status=status, sha256="0" * 64)


def chunks_of(doc, words=400, overlap=0.2):
    return chunk_doc(doc, ChunkingConfig(narrative_words=words, overlap=overlap))


# (text, title, index_text of the chunk text[5:12])
HEADER_CASES = {
    "file and section": ("πριν κείμενο μετά", "Α > Β", "File: notes/os.md\nSection: Α > Β\n\nκείμενο"),
    "no title gives no section line": ("πριν κείμενο μετά", "", "File: notes/os.md\n\nκείμενο"),
}


@pytest.mark.parametrize("text, title, expected", HEADER_CASES.values(), ids=HEADER_CASES.keys())
def test_make_chunk(text, title, expected):
    doc = document(text, content_type="tabular")
    assert make_chunk(doc, 5, 12, title, "naive") == Chunk(
        chunk_id="notes/os.md#5-12", file_path=Path("notes/os.md"), char_start=5, char_end=12, text="κείμενο",
        index_text=expected, content_type="tabular", chunk_strategy="naive")


# stored paths always use / (D11), also for a path that comes from Windows
def test_make_chunk_writes_the_path_with_slashes():
    doc = document("κείμενο")
    # on Windows a Path is a WindowsPath; PureWindowsPath lets the test do the same on Linux
    doc.file_path = PureWindowsPath("Μαθήματα\\ΗΥ252\\os.md")  # pyright: ignore[reportAttributeAccessIssue]
    chunk = make_chunk(doc, 0, 7, "", "content_aware")
    assert (chunk.chunk_id, chunk.index_text) == ("Μαθήματα/ΗΥ252/os.md#0-7", "File: Μαθήματα/ΗΥ252/os.md\n\nκείμενο")


# a document with no text still gives one chunk with only the header, so it is found by its path (D32)
@pytest.mark.parametrize("text, status", [("", "empty"), ("", "unsupported"), ("", "failed"), (" \n\n\t", "ok")],
                         ids=["empty", "unsupported", "failed", "only spaces"])
def test_chunk_doc_without_text(text, status):
    found = chunks_of(document(text, path="Thesis/drafts/v3.zip", content_type="prose", status=status))
    assert [(c.chunk_id, c.char_start, c.char_end, c.text, c.index_text) for c in found] == [
        ("Thesis/drafts/v3.zip#0-0", 0, 0, "", "File: Thesis/drafts/v3.zip\n\n")]


# (text, words, [(text of the chunk, its title)])
DOCUMENT_CASES = {
    "no headings is one chunk": ("Κείμενο.\nΚαι άλλο.", 400, [("Κείμενο.\nΚαι άλλο.", "")]),
    "one chunk per section, also small ones": (
        "Πρόλογος\n\n# Α\nα\n## Β\nβ", 400, [("Πρόλογος", ""), ("# Α\nα", "Α"), ("## Β\nβ", "Α > Β")]),
    "a long section is cut into windows with the same title": (
        "# Α\nα β γ δ ε ζ\n# Β\nβ", 5, [("# Α\nα β γ", "Α"), ("γ δ ε ζ", "Α"), ("# Β\nβ", "Β")]),
    "a comment in code does not start a chunk": (
        "# Α\n```\n# όχι\n```", 400, [("# Α\n```\n# όχι\n```", "Α")]),
}


def header(title, path="notes/os.md"):
    return f"File: {path}\n" + (f"Section: {title}\n" if title else "") + "\n"


@pytest.mark.parametrize("text, words, expected", DOCUMENT_CASES.values(), ids=DOCUMENT_CASES.keys())
def test_chunk_doc(text, words, expected):
    found = chunks_of(document(text), words=words)
    assert [(c.text, c.index_text) for c in found] == [(piece, header(title) + piece) for piece, title in expected]


# the settings of the config reach the windows
def test_chunk_doc_uses_the_config():
    text = " ".join(f"w{i}" for i in range(10))
    assert [c.text for c in chunks_of(document(text), words=4, overlap=0.5)] == [
        "w0 w1 w2 w3", "w2 w3 w4 w5", "w4 w5 w6 w7", "w6 w7 w8 w9"]
    assert [c.text for c in chunks_of(document(text))] == [text]


# every chunk points to its exact text (plan, Step 4: raw_text[char_start:char_end] == text),
# the chunks are in order with unique ids, and carry the type of the document and the strategy
@pytest.mark.parametrize("text", [LONG, MIXED, "\n\n# Α\n\nα  \n"], ids=["long", "mixed", "spaces around"])
def test_chunk_doc_points_to_the_text(text):
    doc = document(text)
    found = chunks_of(doc, words=50)
    assert found
    assert len({c.chunk_id for c in found}) == len(found)
    for i, c in enumerate(found):
        assert doc.raw_text[c.char_start:c.char_end] == c.text
        assert c.text == c.text.strip() and c.text
        assert c.chunk_id == f"notes/os.md#{c.char_start}-{c.char_end}"
        assert c.index_text.startswith("File: notes/os.md\n") and c.index_text.endswith("\n\n" + c.text)
        assert (c.file_path, c.content_type, c.chunk_strategy) == (Path("notes/os.md"), "prose", "content_aware")
        if i > 0:
            assert found[i - 1].char_start < c.char_start


# K4: tables are cut into groups of whole rows, at most `rows` rows and at most `words` words (D111)


def row_groups(text, rows, words, start=0, end=None):
    if end is None:
        end = len(text)
    return [text[s:e] for s, e in split_rows(text, start, end, rows, words)]


# (text, rows, words, [text of each group])
ROW_CASES = {
    "empty text has no groups": ("", 20, 400, []),
    "only spaces and new lines has no groups": (" \n\n\t\n", 20, 400, []),
    "fewer rows than the limit is one group": ("a: 1\nb: 2", 20, 400, ["a: 1\nb: 2"]),
    "exactly the limit is one group": ("a\nb", 2, 400, ["a\nb"]),
    "one row more starts a new group": ("a\nb\nc", 2, 400, ["a\nb", "c"]),
    "groups do not overlap": ("a\nb\nc\nd\ne", 2, 400, ["a\nb", "c\nd", "e"]),
    "the word limit closes a group": ("α β\nγ δ\nε ζ", 20, 4, ["α β\nγ δ", "ε ζ"]),
    "a row longer than the word limit is a group of its own and is not cut": (
        "α\nβ γ δ ε\nζ", 20, 3, ["α", "β γ δ ε", "ζ"]),
    "a first row longer than the word limit": ("α β γ δ\nε", 20, 3, ["α β γ δ", "ε"]),
    "empty lines are not rows": ("a\n\n\nb\n  \nc", 2, 400, ["a\n\n\nb", "c"]),
    "spaces around the group are left out": ("  a: 1  \n b: 2 \n", 20, 400, ["a: 1  \n b: 2"]),
    "the sheet heading counts as a row": ("# Sheet: Α\nx: 1\ny: 2", 2, 400, ["# Sheet: Α\nx: 1", "y: 2"]),
    "only \\n breaks a row": ("a\x0cb\nc", 1, 400, ["a\x0cb", "c"]),
    "a row of one word": ("a\nb", 1, 1, ["a", "b"]),
}


@pytest.mark.parametrize("text, rows, words, expected", ROW_CASES.values(), ids=ROW_CASES.keys())
def test_split_rows(text, rows, words, expected):
    assert row_groups(text, rows, words) == expected


# offsets are positions in the whole text, not in the section
def test_split_rows_inside_a_range():
    text = "πριν\nα: 1\nβ: 2\nμετά"
    start, end = text.index("α"), text.index("\nμετά")
    assert row_groups(text, 1, 400, start, end) == ["α: 1", "β: 2"]


@pytest.mark.parametrize("rows, words", [(0, 400), (-1, 400), (20, 0), (20, -5)],
                         ids=["no rows", "negative rows", "no words", "negative words"])
def test_split_rows_rejects_bad_settings(rows, words):
    with pytest.raises(ValueError):
        row_groups("a\nb", rows, words)


TABLE = "\n".join(f"Όνομα: φοιτητής{i}; Βαθμός: {i % 10}" + "; Σχόλιο: πολύ καλή δουλειά" * (i % 7)
                  + ("\n" if i % 11 == 0 else "") for i in range(300))


# for every setting: the groups are in order, do not overlap, keep whole rows, keep the limits
# (a single long row may pass the word limit), hold every row once, and a group closes only
# when the next row would break a limit
@pytest.mark.parametrize("rows, words", [(20, 400), (20, 30), (5, 1000), (1, 400), (50, 8), (10, 3)])
def test_split_rows_keeps_whole_rows(rows, words):
    found = split_rows(TABLE, 0, len(TABLE), rows, words)
    groups = [[line.strip() for line in TABLE[s:e].split("\n") if line.strip()] for s, e in found]
    assert [line for group in groups for line in group] == [line.strip() for line in TABLE.split("\n") if line.strip()]
    for i, (start, end) in enumerate(found):
        piece = TABLE[start:end]
        assert piece == piece.strip() and piece
        assert start == 0 or TABLE[start - 1] == "\n"
        assert end == len(TABLE) or TABLE[end] == "\n"
        size = sum(len(line.split()) for line in groups[i])
        assert len(groups[i]) <= rows
        assert size <= words or len(groups[i]) == 1
        if i > 0:
            assert found[i - 1][1] < start
            last = groups[i - 1]
            assert len(last) == rows or sum(len(line.split()) for line in last) + len(groups[i][0].split()) > words


def table_chunks(text, rows=20, words=400, path="data/grades.csv"):
    doc = document(text, path=path, content_type="tabular")
    return chunk_doc(doc, ChunkingConfig(narrative_words=words, tabular_rows=rows))


SHEETS = ("# Sheet: Έξοδα\n" + "\n".join(f"Μήνας: {m}; Ποσό: {m * 10}" for m in range(1, 25))
          + "\n\n# Sheet: Έσοδα\nΜήνας: 1; Ποσό: 900")


# a tabular document goes to row groups, one sheet never shares a chunk with another
def test_chunk_doc_cuts_tables_into_rows():
    found = table_chunks(SHEETS, path="data/budget.xlsx")
    assert [(c.text.count("\n") + 1, c.text.split("\n")[0], c.index_text.split("\n")[1]) for c in found] == [
        (20, "# Sheet: Έξοδα", "Section: Sheet: Έξοδα"),
        (5, "Μήνας: 20; Ποσό: 200", "Section: Sheet: Έξοδα"),
        (2, "# Sheet: Έσοδα", "Section: Sheet: Έσοδα")]
    assert all(c.content_type == "tabular" and c.chunk_strategy == "content_aware" for c in found)


# the limits come from the config: tabular_rows and narrative_words
def test_chunk_doc_uses_the_row_settings():
    csv = "\n".join(f"a: {i}; b: {i}" for i in range(10))
    assert [c.text.count("\n") + 1 for c in table_chunks(csv, rows=4)] == [4, 4, 2]
    assert [c.text.count("\n") + 1 for c in table_chunks(csv, words=8)] == [2, 2, 2, 2, 2]


# a csv has no sheet heading, so its chunks have no section line, and rows are not cut into windows
def test_chunk_doc_csv_without_heading():
    csv = "\n".join(f"Όνομα: φοιτητής{i}; Βαθμός: {i}" for i in range(3))
    assert [c.index_text for c in table_chunks(csv, words=8)] == [
        "File: data/grades.csv\n\nΌνομα: φοιτητής0; Βαθμός: 0\nΌνομα: φοιτητής1; Βαθμός: 1",
        "File: data/grades.csv\n\nΌνομα: φοιτητής2; Βαθμός: 2"]


# prose is still cut into word windows with overlap (already true, so not xfail)
def test_chunk_doc_prose_is_not_cut_into_rows():
    text = "\n".join(f"γραμμή {i}" for i in range(30))
    assert len(chunks_of(document(text))) == 1


# K5a: code without a grammar is cut into blocks between empty lines, at most `words` words,
# and a full chunk is cut before the least indented block, so a function is not cut in two (D112)


def blocks(text, words, start=0, end=None):
    if end is None:
        end = len(text)
    return [text[s:e] for s, e in split_blocks(text, start, end, words)]


TWO_FUNCTIONS = "def f():\n    a = 1\n\n    b = 2\n\ndef g():\n    c = 3\n\n    d = 4"

# (text, words, [text of each chunk])
BLOCK_CASES = {
    "empty text has no chunks": ("", 400, []),
    "only spaces and new lines has no chunks": ("  \n\n\t\n", 400, []),
    "small code is one chunk": ("x = 1\ny = 2", 400, ["x = 1\ny = 2"]),
    "exactly the limit stays one chunk": ("x = 1\n\ny = 2", 6, ["x = 1\n\ny = 2"]),
    "empty lines inside a function keep it in one chunk": (
        "def f():\n    a = 1\n\n    b = 2", 400, ["def f():\n    a = 1\n\n    b = 2"]),
    "a full chunk is cut before the next function": (
        TWO_FUNCTIONS, 10, ["def f():\n    a = 1\n\n    b = 2", "def g():\n    c = 3\n\n    d = 4"]),
    "a function is not cut at its empty line": (
        TWO_FUNCTIONS, 14, ["def f():\n    a = 1\n\n    b = 2", "def g():\n    c = 3\n\n    d = 4"]),
    "on a tie the latest place is cut": (
        "def a(): x\n\ndef b(): y\n\ndef c(): z", 7, ["def a(): x\n\ndef b(): y", "def c(): z"]),
    "the rest after a cut can need one more cut": (
        "a\n\nb\n\n    c d e f\n\n    g h i", 6, ["a", "b\n\n    c d e f", "g h i"]),
    "a block bigger than the limit is cut into whole lines": ("a b c\nd e f\ng h", 4, ["a b c", "d e f", "g h"]),
    "a big block closes the chunk before it": ("x\n\na b c\nd e f\n\ny", 4, ["x", "a b c", "d e f", "y"]),
    "lines with only spaces are empty lines": ("x = 1\n   \ny = 2", 3, ["x = 1", "y = 2"]),
    "a hash is not a heading in code": ("# load\nx = 1\n\n# save\ny = 2", 400, ["# load\nx = 1\n\n# save\ny = 2"]),
}


@pytest.mark.parametrize("text, words, expected", BLOCK_CASES.values(), ids=BLOCK_CASES.keys())
def test_split_blocks(text, words, expected):
    assert blocks(text, words) == expected


# offsets are positions in the whole text
def test_split_blocks_inside_a_range():
    text = "πριν\n\nx = 1\n\ny = 2\n\nμετά"
    start, end = text.index("x"), text.index("\n\nμετά")
    assert blocks(text, 3, start, end) == ["x = 1", "y = 2"]


@pytest.mark.parametrize("text", ["x = 1", ""], ids=["code", "empty"])
@pytest.mark.parametrize("words", [0, -1])
def test_split_blocks_rejects_bad_settings(text, words):
    with pytest.raises(ValueError):
        blocks(text, words)


CODE = "\n".join(
    f"def f{i}(x):\n" + "\n".join(f"    y{j} = x + {j}" + ("\n" if j % 4 == 3 else "") for j in range(i % 9 + 1))
    + ("\n\n" + "    # " + "λέξη " * 30 if i % 13 == 0 else "") + "\n"
    for i in range(80))


# for every limit: the chunks are in order, do not overlap, start and end on a line, hold every
# word once, keep the limit (a chunk above it is a single line), and are cut between blocks
@pytest.mark.parametrize("words", [400, 60, 25, 10, 3])
def test_split_blocks_keeps_every_word(words):
    found = split_blocks(CODE, 0, len(CODE), words)
    assert " ".join(CODE[s:e] for s, e in found).split() == CODE.split()
    for i, (start, end) in enumerate(found):
        piece = CODE[start:end]
        assert piece == piece.strip() and piece
        assert start == 0 or CODE[:start].rstrip(" \t").endswith("\n")
        assert end == len(CODE.rstrip()) or CODE[end:].lstrip(" \t").startswith("\n")
        assert len(piece.split()) <= words or "\n" not in piece
        if i > 0:
            assert found[i - 1][1] < start


# K5b: code with a grammar is cut with tree-sitter, like cAST: nodes are joined up to `words` words,
# a bigger node is opened, a line is never cut, and the names go to the section (D112)
# the grammars are read from data/grammars (downloaded once, then used offline)


def code_pieces(text, language, words):
    use_grammars("data/grammars")
    return [(text[s:e], title) for s, e, title in split_code(text, language, words)]


CLASS_A = "class A:\n    def x(self):\n        return 1\n\n    def y(self):\n        return 2"
NAMESPACE = "namespace app {\nint f() { return 1; }\nint g() { return 2; }\n}"
JAVA = "package p;\nimport q.R;\npublic class Main {\n    void a() { x(); }\n    void b() { y(); }\n}"

# (text, language, words, [(text of the chunk, its title)])
CODE_CASES = {
    "empty code has no chunks": ("", "python", 400, []),
    "only spaces has no chunks": ("  \n\n", "python", 400, []),
    "small code is one chunk with the name": ("def f():\n    return 1", "python", 400, [("def f():\n    return 1", "f")]),
    "functions that fit share a chunk": (
        "def f():\n    a = 1\n    b = 2\n\ndef g():\n    c = 3", "python", 400,
        [("def f():\n    a = 1\n    b = 2\n\ndef g():\n    c = 3", "f, g")]),
    "a full chunk closes before the next function": (
        "def f():\n    a = 1\n    b = 2\n\ndef g():\n    c = 3", "python", 10,
        [("def f():\n    a = 1\n    b = 2", "f"), ("def g():\n    c = 3", "g")]),
    "a big class is opened into its methods": (
        CLASS_A, "python", 6,
        [("class A:\n    def x(self):\n        return 1", "A > x"), ("def y(self):\n        return 2", "A > y")]),
    "a big function is cut between statements and keeps its name": (
        "def f():\n    a = 1\n    b = 2\n    c = 3", "python", 7, [("def f():\n    a = 1", "f"), ("b = 2\n    c = 3", "f")]),
    "decorators belong to the function": ("@cache\ndef f():\n    return 1", "python", 400, [("@cache\ndef f():\n    return 1", "f")]),
    "a hash comment is not a heading": ("# load\nx = 1", "python", 400, [("# load\nx = 1", "")]),
    "c: the name is inside the declarator": ("int main(void) {\n    return 0;\n}", "c", 400, [("int main(void) {\n    return 0;\n}", "main")]),
    "c: a function that returns a pointer": (
        "static char *name(int x) {\n    return 0;\n}", "c", 400, [("static char *name(int x) {\n    return 0;\n}", "name")]),
    "c++: a method written outside its class": (
        "int Parser::read(int x) {\n    return x;\n}", "cpp", 400, [("int Parser::read(int x) {\n    return x;\n}", "Parser::read")]),
    "c++: a small namespace is one chunk": ("namespace app {\nint f() { return 1; }\n}", "cpp", 400,
                                           [("namespace app {\nint f() { return 1; }\n}", "app")]),
    "c++: a big namespace gives a path of names": (
        NAMESPACE, "cpp", 8,
        [("namespace app {", "app"), ("int f() { return 1; }", "app > f"), ("int g() { return 2; }\n}", "app > g")]),
    "java: a class": ("public class Main {\n    void a() { }\n}", "java", 400, [("public class Main {\n    void a() { }\n}", "Main")]),
    "java: a big class is cut at whole lines": (
        JAVA, "java", 6,
        [("package p;\nimport q.R;", ""), ("public class Main {", "Main"), ("void a() { x(); }", "Main > a"),
         ("void b() { y(); }\n}", "Main > b")]),
    "c#: a class": ("class Program {\n    static void Main() { }\n}", "csharp", 400,
                    [("class Program {\n    static void Main() { }\n}", "Program")]),
    "greek letters do not move the positions": (
        "def f():\n    return 'γειά'\n\ndef g():\n    return 'κόσμε'", "python", 4,
        [("def f():\n    return 'γειά'", "f"), ("def g():\n    return 'κόσμε'", "g")]),
    "a long comment is cut into whole lines": (
        "/* a b c\nd e f\ng h */\nint x;", "c", 4, [("/* a b c", ""), ("d e f", ""), ("g h */", ""), ("int x;", "")]),
    "a line is never cut": (
        "int f() { if (a) { x(); } else { y(); } }", "c", 3, [("int f() { if (a) { x(); } else { y(); } }", "f")]),
    "a string keeps the words between its escapes": (
        'x = "a b c\\n d e f"', "python", 3, [('x = "a b c\\n d e f"', "")]),
    "a function opened on a shared line keeps its name": (
        "int x; int g(void) {\n    a = 1;\n    b = 2;\n}", "c", 11,
        [("int x; int g(void) {\n    a = 1;\n    b = 2;", "g"), ("}", "g")]),
    "a chunk without a function takes the deepest path": (
        "x = 1\nclass A:\n    y = 2\n    z = 3\n    w = 4", "python", 8,
        [("x = 1\nclass A:\n    y = 2", "A"), ("z = 3\n    w = 4", "A")]),
}


# a long string over many lines keeps every word, also the ones between its escapes
def test_split_code_long_string_keeps_every_word():
    text = 'x = """a b\\n c d\ne f\\n g h\ni j\\t k l\nm n"""\ny = 2'
    assert " ".join(piece for piece, _ in code_pieces(text, "python", 3)).split() == text.split()


@pytest.mark.parametrize("text, language, words, expected", CODE_CASES.values(), ids=CODE_CASES.keys())
def test_split_code(text, language, words, expected):
    assert code_pieces(text, language, words) == expected


@pytest.mark.parametrize("text", ["x = 1", ""], ids=["code", "empty"])
def test_split_code_rejects_bad_settings(text):
    with pytest.raises(ValueError):
        code_pieces(text, "python", 0)


# (extension, language): the language comes from the extension, and .h is read as C++ (D112)
@pytest.mark.parametrize("ext, language", [(".py", "python"), (".PY", "python"), (".c", "c"), (".h", "cpp"), (".H", "cpp"),
                                           (".cpp", "cpp"), (".cxx", "cpp"), (".java", "java"), (".cs", "csharp"),
                                           (".alpha", None), ("", None)])
def test_code_language(ext, language):
    assert code_language(ext) == language


C_CODE = "\n".join(
    f"/* function {i} */\nstatic int f{i}(int x) {{\n" + "".join(f"    x = x * {j} + {i};\n" for j in range(i % 11 + 1)) + "    return x;\n}\n"
    for i in range(60))
JAVA_CODE = "package p;\n\npublic class Big {\n" + "".join(
    f"    public int m{i}(int x) {{\n" + "".join(f"        x += {j};\n" for j in range(i % 9 + 1)) + "        return x;\n    }\n\n"
    for i in range(50)) + "}\n"


# for every file and limit: every word once and in order, the chunks are in order, start and end at
# whole lines, keep the limit, and a function that fits in the limit is never cut in two
@pytest.mark.parametrize("words", [400, 80, 30])
@pytest.mark.parametrize("text, language, function, function_end", [
    (CODE, "python", r"def f\d+", "\ndef "), (C_CODE, "c", r"static int f\d+", "\n}\n"),
    (JAVA_CODE, "java", r"public int m\d+", "\n    }\n")], ids=["python", "c", "java"])
def test_split_code_keeps_functions_whole(text, language, function, function_end, words):
    use_grammars("data/grammars")
    found = split_code(text, language, words)
    assert " ".join(text[s:e] for s, e, _ in found).split() == text.split()
    for i, (start, end, title) in enumerate(found):
        piece = text[start:end]
        assert piece == piece.strip() and piece and isinstance(title, str)
        assert start == 0 or text[:start].rstrip(" \t").endswith("\n")
        assert end == len(text.rstrip()) or text[end:].lstrip(" \t").startswith("\n")
        assert len(piece.split()) <= words or "\n" not in piece  # only a single long line can pass the limit
        if i > 0:
            assert found[i - 1][1] < start
    starts = [start for start, _, _ in found]
    for match in re.finditer(function, text):
        begin = match.start()
        close = text.find(function_end, begin)
        close = len(text) if close == -1 else close
        if len(text[begin:close].split()) < words:
            assert not any(begin < s < close for s in starts), match.group()


def code_chunks(text, path, words=400):
    return chunk_doc(document(text, path=path, content_type="code"), ChunkingConfig(narrative_words=words))


# a code file gets the names of its functions in the section, and is never cut at "headings"
def test_chunk_doc_code_with_a_grammar():
    text = "# Τίτλος που δεν είναι επικεφαλίδα\nimport os\n\ndef f():\n    return 1\n\ndef g():\n    return 2"
    assert [c.index_text for c in code_chunks(text, "src/app.py")] == [f"File: src/app.py\nSection: f, g\n\n{text}"]


# the word limit of code comes from the config
def test_chunk_doc_code_uses_the_config():
    text = "def f():\n    a = 1\n    b = 2\n\ndef g():\n    c = 3"
    assert [c.index_text.split("\n")[1] for c in code_chunks(text, "src/app.py", words=10)] == ["Section: f", "Section: g"]


# .h files are read as C++: the class gets its name
def test_chunk_doc_header_file_is_cpp():
    text = "class Reader {\npublic:\n    int read() { return 1; }\n};"
    assert [c.index_text.split("\n")[1] for c in code_chunks(text, "include/reader.h")] == ["Section: Reader"]


# code without a grammar goes to blocks between empty lines, without a section
def test_chunk_doc_code_without_a_grammar():
    text = "# a\nx 1\n\n# b\ny 2"
    assert [(c.text, c.index_text) for c in code_chunks(text, "data/run.alpha", words=4)] == [
        ("# a\nx 1", "File: data/run.alpha\n\n# a\nx 1"), ("# b\ny 2", "File: data/run.alpha\n\n# b\ny 2")]


# a grammar that cannot be loaded is like no grammar: the file still gets chunks
def test_chunk_doc_code_when_the_grammar_fails(monkeypatch):
    monkeypatch.setattr(chunker, "code_parser", lambda language: None)
    text = "def f():\n    return 1"
    assert [c.index_text for c in code_chunks(text, "src/app.py")] == [f"File: src/app.py\n\n{text}"]


# the grammars folder is part of the chunking config
def test_chunking_config_has_the_grammars_folder():
    assert ChunkingConfig().grammars == "data/grammars"


# K6a: the naive strategy (fixed word windows for every type, for the ablation) and the chunk length table (D113)

def naive_chunks(text, path="notes/os.md", content_type="prose", words=400, overlap=0.2):
    return chunk_doc(document(text, path=path, content_type=content_type),
                     ChunkingConfig(strategy="naive", narrative_words=words, overlap=overlap))


# (text, path, content_type, words, [text of each chunk]): the same word windows for every type,
# without sections, rows or functions
NAIVE_CASES = {
    "headings do not start chunks": (
        "Πρόλογος\n\n# Α\nα\n## Β\nβ", "notes/os.md", "prose", 400, ["Πρόλογος\n\n# Α\nα\n## Β\nβ"]),
    "a long text is cut every few words": (
        "# Α\nα β γ δ ε ζ\n# Β\nβ", "notes/os.md", "prose", 5, ["# Α\nα β γ", "γ δ ε ζ\n#", "# Β\nβ"]),
    "rows of a table are cut": (
        "# Sheet: Α\nΜήνας: 1; Ποσό: 10\nΜήνας: 2; Ποσό: 20", "data/a.xlsx", "tabular", 6,
        ["# Sheet: Α\nΜήνας: 1; Ποσό:", "Ποσό: 10\nΜήνας: 2; Ποσό: 20"]),
    "functions are cut": (
        "def f():\n    a = 1\n    b = 2\n\ndef g():\n    c = 3", "src/app.py", "code", 6,
        ["def f():\n    a = 1\n    b", "b = 2\n\ndef g():\n    c", "c = 3"]),
}


@pytest.mark.parametrize("text, path, content_type, words, expected", NAIVE_CASES.values(), ids=NAIVE_CASES.keys())
def test_chunk_doc_naive(text, path, content_type, words, expected):
    found = naive_chunks(text, path=path, content_type=content_type, words=words)
    assert [(c.text, c.index_text) for c in found] == [(piece, header("", path) + piece) for piece in expected]
    assert {(c.content_type, c.chunk_strategy) for c in found} == {(content_type, "naive")}


# the naive windows take the words and the overlap of the config; the heading is only words, not a section
def test_chunk_doc_naive_uses_the_config():
    text = "# Α\n" + " ".join(f"w{i}" for i in range(8))
    assert [(c.text, c.index_text) for c in naive_chunks(text, words=4, overlap=0.5)] == [
        (piece, header("") + piece) for piece in ["# Α\nw0 w1", "w0 w1 w2 w3", "w2 w3 w4 w5", "w4 w5 w6 w7"]]
    assert [c.text for c in naive_chunks(text, words=4, overlap=0)] == ["# Α\nw0 w1", "w2 w3 w4 w5", "w6 w7"]


# a document with no text gives the same single chunk with only the header, marked naive
# (already true, because the empty check comes first: keep it first)
@pytest.mark.parametrize("text", ["", " \n\t"], ids=["empty", "only spaces"])
def test_chunk_doc_naive_without_text(text):
    found = naive_chunks(text, path="Thesis/drafts/v3.zip")
    assert [(c.chunk_id, c.text, c.index_text, c.chunk_strategy) for c in found] == [
        ("Thesis/drafts/v3.zip#0-0", "", "File: Thesis/drafts/v3.zip\n\n", "naive")]


# the naive strategy never parses code, so it works even when no grammar can be loaded
def test_chunk_doc_naive_does_not_parse_code(monkeypatch):
    def no_parser(language):
        raise AssertionError("the naive strategy must not parse code")
    monkeypatch.setattr(chunker, "code_parser", no_parser)
    monkeypatch.setattr(chunker, "use_grammars", no_parser)
    text = "def f():\n    return 1"
    assert [c.text for c in naive_chunks(text, path="src/app.py", content_type="code")] == [text]


# every naive chunk points to its exact text, has no section line, and the chunks are the word windows of
# the whole text, whatever the type (each text starts with a heading, which the naive strategy ignores)
@pytest.mark.parametrize("content_type", ["prose", "tabular", "code"])
@pytest.mark.parametrize("text", [LONG, MIXED, TABLE, CODE], ids=["long", "mixed", "table", "code"])
def test_chunk_doc_naive_points_to_the_text(text, content_type):
    text = "# Τίτλος\n\n" + text
    doc = document(text, path="a/b.txt", content_type=content_type)
    found = chunk_doc(doc, ChunkingConfig(strategy="naive", narrative_words=50))
    assert [(c.char_start, c.char_end) for c in found] == split_windows(text, 0, len(text), 50, 0.2)
    for c in found:
        assert doc.raw_text[c.char_start:c.char_end] == c.text
        assert c.text == c.text.strip() and c.text
        assert c.index_text == f"File: a/b.txt\n\n{c.text}"
    assert [c.char_start for c in found] == sorted({c.char_start for c in found})  # in order, no two the same


def chunks_with(*texts):
    return [make_chunk(document(text), 0, len(text), "", "content_aware") for text in texts]


def of_words(*counts):
    return chunks_with(*(" ".join(["w"] * n) for n in counts))  # n words are 2n - 1 characters


# the example of the explanation: mean 319, median 385 (between the 5th and the 6th), p90 400
def test_length_stats():
    stats = length_stats(of_words(50, 120, 300, 350, 380, 390, 400, 400, 400, 400))
    assert stats == {"chunks": 10, "empty": 0,
                     "chars": {"mean": 637, "median": 769, "p90": 799},
                     "words": {"mean": 319, "median": 385, "p90": 400}}


# p90 is the length of a real chunk: the smallest one with at least 90% of the chunks at or below it
@pytest.mark.parametrize("counts, p90", [
    ([5], 5),
    (range(1, 11), 9),
    (range(1, 12), 10),
    (range(1, 21), 18),
    (range(1, 26), 23),
    (range(1, 31), 27),
    ([3, 1, 2], 3),
    ([400] * 9 + [1], 400),
], ids=["one chunk", "10 chunks", "11 chunks: 9.9 goes up", "20 chunks", "25 chunks: 22.5 goes up",
        "30 chunks: a real chunk, not between two", "not in order", "small last"])
def test_length_stats_p90(counts, p90):
    assert length_stats(of_words(*counts))["words"]["p90"] == p90


# the median of an odd count is the middle chunk; the mean is not rounded
def test_length_stats_median_and_mean():
    words = length_stats(of_words(1, 10, 2))["words"]
    assert words["median"] == 2
    assert words["mean"] == pytest.approx(13 / 3)
    assert length_stats(of_words(1, 2))["words"]["median"] == pytest.approx(1.5)


# chunks of documents without text are counted, but they do not pull the lengths down
def test_length_stats_without_text():
    found = of_words(10, 20) + chunks_with("")
    assert length_stats(found) == {"chunks": 3, "empty": 1,
                                   "chars": {"mean": 29, "median": 29, "p90": 39},
                                   "words": {"mean": 15, "median": 15, "p90": 20}}
    zero = {"mean": 0, "median": 0, "p90": 0}
    assert length_stats(chunks_with("", "")) == {"chunks": 2, "empty": 2, "chars": zero, "words": zero}
    assert length_stats([]) == {"chunks": 0, "empty": 0, "chars": zero, "words": zero}


# words are counted as everywhere in the chunker: anything between spaces, tabs or new lines
def test_length_stats_counts_words_and_characters():
    stats = length_stats(chunks_with("α\tβ\n\nγ,δ  ε"))
    assert (stats["words"]["mean"], stats["chars"]["mean"]) == (4, 11)
