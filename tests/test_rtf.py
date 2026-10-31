import pytest

from rag_fs.ingest.parsers.rtf import read_rtf
from test_parsers import parse_one

NBSP = chr(0xa0)
ENDASH, EMDASH, BULLET = chr(0x2013), chr(0x2014), chr(0x2022)
LQ, RQ, LDQ, RDQ = chr(0x2018), chr(0x2019), chr(0x201c), chr(0x201d)


# an rtf document around `body`, with `head` (font table, style sheet) before it
def rtf(body, head=""):
    return r"{\rtf1\ansi\ansicpg1252\deff0" + head + " " + body + "}"


def read(tmp_path, text, encoding="ascii"):
    f = tmp_path / "doc.rtf"
    f.write_bytes(text.encode(encoding))
    return read_rtf(f)


# the reader is written in four pieces; what a piece adds is xfail until that piece is written
TEXT = pytest.mark.xfail(strict=True, reason="rtf piece 1: text")
LINKS = pytest.mark.xfail(strict=True, reason="rtf piece 2: links")
TABLES = pytest.mark.xfail(strict=True, reason="rtf piece 3: tables")
HEADINGS = pytest.mark.xfail(strict=True, reason="rtf piece 4: headings")


# the cases of a dict as pytest params, marked with `mark` unless they already pass
def cases(table, mark, guards=()):
    return [pytest.param(*value, id=key, marks=() if key in guards else mark) for key, value in table.items()]


GREEK_FONTS = r"{\fonttbl{\f0\fswiss Arial;}{\f1\fswiss\fcharset161 Arial Greek;}}"
STYLES = (r"{\stylesheet{\s0 Normal;}{\s1\outlinelevel0 heading 1;}{\s2\sbasedon0 heading 2;}"
          r"{\s15 Title;}{\s20 Quote;}}")

# piece 1, text: (body, head, text)
TEXT_CASES = {
    "every paragraph is a block": (r"First.\par Second.\par", "", "First.\n\nSecond."),
    "empty paragraphs leave no gaps": (r"\par\par First.\par\par\par Second.\par\par", "", "First.\n\nSecond."),
    "line break inside a paragraph": (r"One\line two\par", "", "One\ntwo"),
    "tab": (r"Name\tab Grade\par", "", "Name\tGrade"),
    "backslash and new line is a paragraph": ("One\\\nTwo", "", "One\n\nTwo"),
    "new lines in the file are not text": ("Hel\r\nlo wor\nld\\par", "", "Hello world"),
    "formatting groups keep their text": (r"{\b bold} and {\i\ul italic}\par", "", "bold and italic"),
    "symbols": (r"A\~B x\endash y\emdash z \lquote q\rquote  \ldblquote dq\rdblquote  \bullet  hy\-phen non\_stop \{b\} c\\d\par", "",
                f"A{NBSP}B x{ENDASH}y{EMDASH}z {LQ}q{RQ} {LDQ}dq{RDQ} {BULLET} hyphen non-stop {{b}} c\\d"),
    "unicode with one fallback character": (r"\uc1\u915?\u949?\u953?\u945? \u963?\u959?\u965?\par", "", "Γεια σου"),
    "unicode with two fallback characters": (r"\uc2\u915\'c3\'c3\u949 xx!\par", "", "Γε!"),
    "unicode with no fallback": (r"\uc0\u915\u949 x\par", "", "Γεx"),
    "fallback count of a group ends with it": (r"{\uc2\u915 xx}\u949?\par", "", "Γε"),
    "greek of an old word: a greek font in a latin file": (r"\f0 Hello \f1 \'c3\'e5\'e9\'e1\par", GREEK_FONTS, "Hello Γεια"),
    "the font of a group ends with it": (r"\f0 {\f1 \'e1}\'e1\par", GREEK_FONTS, "α" + chr(0xe1)),
    "settings, styles and generator are not text": (
        r"{\colortbl;\red255\green0\blue0;}{\*\generator Riched20 10.0;}Body\par", STYLES, "Body"),
    "headers, footers, footnotes, document info": (
        r"{\header Head}{\footer Foot}{\info{\title Secret}}Main{\super\chftn{\footnote\pard Note.}} text.\par", "", "Main text."),
    "bullets of lists are not text": (r"{\listtext\'b7\tab}First\par{\pntext\'b7\tab}Second\par", "", "First\n\nSecond"),
    "pictures and objects": (r"Before {\pict\pngblip 89504e470d0a}{\object{\*\objdata 0102}}after\par", "", "Before after"),
    "unknown parts marked with star": (r"Text{\*\unknownthing secret}\par", "", "Text"),
    "text after the last paragraph": (r"First.\par Last", "", "First.\n\nLast"),
}


@pytest.mark.parametrize("body, head, expected", cases(TEXT_CASES, TEXT, ['line break inside a paragraph', 'tab', 'new lines in the file are not text', 'formatting groups keep their text', 'unicode with one fallback character', 'unicode with two fallback characters', 'unicode with no fallback', 'fallback count of a group ends with it', 'settings, styles and generator are not text', 'headers, footers, footnotes, document info', 'pictures and objects', 'unknown parts marked with star']))
def test_read_rtf_text(tmp_path, body, head, expected):
    assert read(tmp_path, rtf(body, head)) == expected


# greek typed straight into the file in the code page of the file
def test_read_rtf_greek_in_the_code_page(tmp_path):
    text = "{\\rtf1\\ansi\\ansicpg1253 Γεια σου κόσμε\\par}"
    assert read(tmp_path, text, "cp1253") == "Γεια σου κόσμε"


# piece 2, links: (body, text)
LINK_CASES = {
    "link with its address": (r'See {\field{\*\fldinst HYPERLINK "https://www.csd.uoc.gr"}{\fldrslt the site}} now.\par',
                              "See the site (https://www.csd.uoc.gr) now."),
    "instruction in pieces and result with formatting": (
        r'{\field{\*\fldinst {HYPERLINK }{"https://www.uoc.gr/a" \\o "tip"}}{\fldrslt {\ul University}}}\par',
        "University (https://www.uoc.gr/a)"),
    "link to a place in the document": (r'{\field{\*\fldinst HYPERLINK \\l "_Toc12"}{\fldrslt Chapter 2}}\par', "Chapter 2"),
    "address that is the text": (r'{\field{\*\fldinst HYPERLINK "https://www.uoc.gr"}{\fldrslt www.uoc.gr}}\par', "www.uoc.gr"),
    "other fields keep their result": (r"Page {\field{\*\fldinst PAGE}{\fldrslt 3}} of 9\par", "Page 3 of 9"),
    "field without a result": (r"A{\field{\*\fldinst PAGE}}B\par", "AB"),
    "two links in a paragraph": (
        r'{\field{\*\fldinst HYPERLINK "https://a.gr"}{\fldrslt A}} and {\field{\*\fldinst HYPERLINK "https://b.gr"}{\fldrslt B}}\par',
        "A (https://a.gr) and B (https://b.gr)"),
}


@pytest.mark.parametrize("body, expected", cases(LINK_CASES, LINKS, ['link to a place in the document', 'address that is the text', 'other fields keep their result', 'field without a result']))
def test_read_rtf_links(tmp_path, body, expected):
    assert read(tmp_path, rtf(body)) == expected


# piece 3, tables
def row(*cells, defs="", table=True):
    cellx = "".join(rf"{d}\cellx{2000 * (i + 1)}" for i, d in enumerate(defs or [""] * len(cells)))
    inside = r"\intbl " if table else " "
    return r"\trowd" + cellx + r"\pard" + inside + "".join(rf"{c}\cell " for c in cells) + r"\row "


TABLE_CASES = {
    "rows of a table with text around": (
        r"Before\par " + row("Name", "Grade") + row("Maria", "9") + row("Nikos", "7") + r"\pard After\par",
        "Before\n\nName: Maria; Grade: 9\nName: Nikos; Grade: 7\n\nAfter"),
    "old table without intbl": (r"\trowd\cellx2000\cellx4000 Name\cell Grade\cell\row\trowd\cellx2000\cellx4000 Maria\cell 9\cell\row\pard After table\par",
                                "Name: Maria; Grade: 9\n\nAfter table"),
    "cell of two paragraphs": (row("Name", "Notes") + row("Maria", r"first line\par second line"),
                               "Name: Maria; Notes: first line / second line"),
    "header row that only has numbers": (
        r"\trowd\trhdr\cellx2000\cellx4000\pard\intbl 2023\cell 2024\cell\row " + row("8", "9"), "2023: 8; 2024: 9"),
    "cell merged with the one above": (
        row("Term", "Course") + row("1st", "Logic", defs=[r"\clvmgf", ""]) + row("", "Analysis", defs=[r"\clvmrg", ""]),
        "Term: 1st; Course: Logic\nTerm: 1st; Course: Analysis"),
    "two tables": (row("A", "B") + row("1", "2") + r"\pard Between\par " + row("C", "D") + row("3", "4"),
                   "A: 1; B: 2\n\nBetween\n\nC: 3; D: 4"),
    "empty cells": (row("A", "B", "C") + row("1", "", "3"), "A: 1; C: 3\nB"),
    "table at the very end": (r"Before\par " + row("A", "B") + row("1", "2"), "Before\n\nA: 1; B: 2"),
}


@pytest.mark.parametrize("body, expected", cases(TABLE_CASES, TABLES, []))
def test_read_rtf_tables(tmp_path, body, expected):
    assert read(tmp_path, rtf(body)) == expected


# piece 4, headings from the styles: (body, text)
HEADING_CASES = {
    "headings of the style sheet": (r"\pard\s1 Intro\par\pard\s2 Part\par\pard\s0 Body\par", "# Intro\n\n## Part\n\nBody"),
    "title": (r"\pard\s15 My notes\par\pard Text\par", "# My notes\n\nText"),
    "a style that is not a heading": (r"\pard\s20 Said someone\par", "Said someone"),
    "outline level of the paragraph": (r"\pard\outlinelevel2 Deep\par\pard Body\par", "### Deep\n\nBody"),
    "outline level 9 is body text": (r"\pard\s1\outlinelevel9 Plain\par", "Plain"),
    "pard ends the heading": (r"\pard\s1 Intro\par Still heading\par\pard Body\par", "# Intro\n\n# Still heading\n\nBody"),
    "heading of two lines": (r"\pard\s1 First\line second\par", "# First second"),
    "heading style inside a table is a cell": (row("Name", r"\s1 Grade") + row("Maria", "9"), "Name: Maria; Grade: 9"),
    "style that is not in the style sheet": (r"\pard\s7 Text\par", "Text"),
}


@pytest.mark.parametrize("body, expected", cases(HEADING_CASES, HEADINGS, ['a style that is not a heading', 'outline level 9 is body text', 'style that is not in the style sheet']))
def test_read_rtf_headings(tmp_path, body, expected):
    assert read(tmp_path, rtf(body, STYLES)) == expected


# through parse_file: prose, and the no-break space becomes a space
def test_parse_file_rtf(tmp_path):
    doc = parse_one(tmp_path, "notes.rtf", rtf(r"A\~B\par").encode())
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ok", "prose", "A B")
