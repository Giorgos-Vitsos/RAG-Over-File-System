from pathlib import Path, PureWindowsPath

import pytest

from rag_fs.ingest.chunker import split_sections, split_windows


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
# xfail until make_chunk and chunk_document exist; remove the marks after review
K3 = pytest.mark.xfail(strict=True, reason="K3: make_chunk and chunk_document are not written yet")


def document(text, path="notes/os.md", content_type="prose", status="ok"):
    from rag_fs.models import Document  # goes to the top of the file after review
    return Document(file_path=Path(path), root_id="root_0", ext=Path(path).suffix, raw_text=text,
                    last_modified=0.0, content_type=content_type, parse_status=status, sha256="0" * 64)


def chunks_of(doc, words=400, overlap=0.2):
    from rag_fs.config import ChunkingConfig  # goes to the top of the file after review
    from rag_fs.ingest.chunker import chunk_document  # goes to the top of the file after review
    return chunk_document(doc, ChunkingConfig(narrative_words=words, overlap=overlap))


# (text, title, index_text of the chunk text[5:12])
HEADER_CASES = {
    "file and section": ("πριν κείμενο μετά", "Α > Β", "File: notes/os.md\nSection: Α > Β\n\nκείμενο"),
    "no title gives no section line": ("πριν κείμενο μετά", "", "File: notes/os.md\n\nκείμενο"),
}


@K3
@pytest.mark.parametrize("text, title, expected", HEADER_CASES.values(), ids=HEADER_CASES.keys())
def test_make_chunk(text, title, expected):
    from rag_fs.ingest.chunker import make_chunk  # goes to the top of the file after review
    from rag_fs.models import Chunk  # goes to the top of the file after review
    doc = document(text, content_type="tabular")
    assert make_chunk(doc, 5, 12, title, "naive") == Chunk(
        chunk_id="notes/os.md#5-12", file_path=Path("notes/os.md"), char_start=5, char_end=12, text="κείμενο",
        index_text=expected, content_type="tabular", chunk_strategy="naive")


# stored paths always use / (D11), also for a path that comes from Windows
@K3
def test_make_chunk_writes_the_path_with_slashes():
    from rag_fs.ingest.chunker import make_chunk
    doc = document("κείμενο")
    doc.file_path = PureWindowsPath("Μαθήματα\\ΗΥ252\\os.md")
    chunk = make_chunk(doc, 0, 7, "", "content_aware")
    assert (chunk.chunk_id, chunk.index_text) == ("Μαθήματα/ΗΥ252/os.md#0-7", "File: Μαθήματα/ΗΥ252/os.md\n\nκείμενο")


# a document with no text still gives one chunk with only the header, so it is found by its path (D32)
@K3
@pytest.mark.parametrize("text, status", [("", "empty"), ("", "unsupported"), ("", "failed"), (" \n\n\t", "ok")],
                         ids=["empty", "unsupported", "failed", "only spaces"])
def test_chunk_document_without_text(text, status):
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


@K3
@pytest.mark.parametrize("text, words, expected", DOCUMENT_CASES.values(), ids=DOCUMENT_CASES.keys())
def test_chunk_document(text, words, expected):
    found = chunks_of(document(text), words=words)
    assert [(c.text, c.index_text) for c in found] == [(piece, header(title) + piece) for piece, title in expected]


# the settings of the config reach the windows
@K3
def test_chunk_document_uses_the_config():
    text = " ".join(f"w{i}" for i in range(10))
    assert [c.text for c in chunks_of(document(text), words=4, overlap=0.5)] == [
        "w0 w1 w2 w3", "w2 w3 w4 w5", "w4 w5 w6 w7", "w6 w7 w8 w9"]
    assert [c.text for c in chunks_of(document(text))] == [text]


# every chunk points to its exact text (plan, Step 4: raw_text[char_start:char_end] == text),
# the chunks are in order with unique ids, and carry the type of the document and the strategy
@K3
@pytest.mark.parametrize("text", [LONG, MIXED, "\n\n# Α\n\nα  \n"], ids=["long", "mixed", "spaces around"])
def test_chunk_document_points_to_the_text(text):
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
