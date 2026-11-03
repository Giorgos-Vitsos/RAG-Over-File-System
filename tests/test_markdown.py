import pytest

from rag_fs.ingest.parsers import PARSERS
from rag_fs.ingest.parsers.markdown import read_markdown
from test_parsers import EXPECTED_DOCX, parse_one


# writes a markdown string to a file and reads it
def read_md(tmp_path, source, name="notes.md"):
    f = tmp_path / name
    f.write_bytes(source.encode())
    return read_markdown(f)


# the same document as make_docx in test_parsers.py, written in markdown
DOC_MD = """# Εισαγωγή

Πρώτη παράγραφος.
Με δεύτερη γραμμή.

| Όνομα | Βαθμός |
|---|---|
| Μαρία | 9 |

## Μέθοδος

![Αρχιτεκτονική του συστήματος](arch.png)

![](empty.png)

Εικόνα 1: Η ροή των δεδομένων

#### Λεπτομέρειες

Τελευταία παράγραφος.
"""


# the same content gives the same text in markdown and in docx
def test_read_markdown_gives_the_same_text_as_docx(tmp_path):
    assert read_md(tmp_path, DOC_MD) == EXPECTED_DOCX


# (markdown source, text)
MD_CASES = {
    "heading with bold and a google anchor": ("# **Τίτλος** {#titlos}", "# Τίτλος"),
    "underlined heading": ("Τίτλος\n======\n\nΥπότιτλος\n---------", "# Τίτλος\n\n## Υπότιτλος"),
    "escaped characters": ("το w10\\_GUI\\_Events και 1\\. τέλος", "το w10_GUI_Events και 1. τέλος"),
    "bold, italic, strike and code": ("**α** *β* ~~γ~~ `δ()`", "α β γ δ()"),
    "link keeps its address": ("Δες τον [οδηγό](https://x.gr/guide).", "Δες τον οδηγό (https://x.gr/guide)."),
    "link whose text is the address": ("[https\\://elearn.uoc.gr](https://elearn.uoc.gr)", "https://elearn.uoc.gr"),
    "address in angle brackets": ("<https://x.gr> και <maria@uoc.gr>", "https://x.gr και maria@uoc.gr"),
    "link by reference": ("Δες τον [οδηγό][1].\n\n[1]: https://x.gr/guide", "Δες τον οδηγό (https://x.gr/guide)."),
    "picture with a title only": ('![](logo.png "Λογότυπο")', "[Image: Λογότυπο]"),
    "picture without alt text": ("![](logo.png)", ""),
    "picture before text in a paragraph": ("![Σχήμα](a.png) Η ροή", "[Image: Σχήμα]\n\nΗ ροή"),
    "hard, soft and html line breaks": ("α  \nβ\nγ<br>δ", "α\nβ\nγ\nδ"),
    "two breaks in a row": ("α<br><br>β", "α\n\nβ"),
    "list items are blocks": ("- α\n- β\n  - γ\n\n1. δ\n2. ε", "α\n\nβ\n\nγ\n\nδ\n\nε"),
    "quote": ("> σημείωση\n> σε δύο γραμμές", "σημείωση\nσε δύο γραμμές"),
    "table": ("| Όνομα | Βαθμός |\n|---|---|\n| **Μαρία** | 9 |\n| Νίκος | 7 |",
              "Όνομα: Μαρία; Βαθμός: 9\nΌνομα: Νίκος; Βαθμός: 7"),
    "link and line break in a cell": ("| Όνομα | Σελίδα |\n|---|---|\n| Μαρία | [site](https://x.gr)<br>παλιά |",
                                      "Όνομα: Μαρία; Σελίδα: site (https://x.gr) / παλιά"),
    "table inside a list": ("- Βαθμοί:\n\n  | Όνομα | Βαθμός |\n  |---|---|\n  | Μαρία | 9 |",
                            "Βαθμοί:\n\nΌνομα: Μαρία; Βαθμός: 9"),
    "code block is kept as it is": ("```python\ndef f(x):\n    return x  # **όχι** έντονα\n```",
                                    "def f(x):\n    return x  # **όχι** έντονα"),
    "indented code block": ("Κείμενο\n\n    x = 1\n        y = 2", "Κείμενο\n\nx = 1\n    y = 2"),
    "html block": ("<div>κείμενο <b>σε</b> html</div>", "κείμενο σε html"),
    "horizontal rule": ("α\n\n---\n\nβ", "α\n\nβ"),
    "google table of contents": ("[Σύνδεσμοι\t1](#σύνδεσμοι)\n\n[**Ασκήσεις\t1**](#ασκήσεις)\n\nΚείμενο", "Κείμενο"),
    "list of links inside the file": ("- [Εισαγωγή](#intro)\n- [Μέθοδος](#method)\n\nΚείμενο", "Κείμενο"),
    "inner link inside a sentence is text": ("Δες την [Εισαγωγή](#intro) για αρχή.", "Δες την Εισαγωγή για αρχή."),
    "settings at the top of the file": ("---\ntitle: Σημειώσεις\ntags: [ΗΥ252]\n---\n# Εισαγωγή\nΚείμενο",
                                        "title: Σημειώσεις\ntags: [ΗΥ252]\n\n# Εισαγωγή\n\nΚείμενο"),
    "settings only, no new line at the end": ("---\ntitle: Σημειώσεις\n---", "title: Σημειώσεις"),
    "windows line endings": ("# Τίτλος\r\n\r\nα  \r\nβ", "# Τίτλος\n\nα\nβ"),
    "heading with a line break": ("# α<br>β", "# α β"),
    "two anchors in a heading": ("# α {#x} β {#y}", "# α {#x} β"),
    "picture in a heading": ("# ![Λογότυπο](a.png) Τίτλος", "[Image: Λογότυπο]\n\n# Τίτλος"),
    "picture in a cell": ("| Όνομα | Σήμα |\n|---|---|\n| Μαρία | ![Λογότυπο](a.png) |", "Όνομα: Μαρία; Σήμα: [Image: Λογότυπο]"),
    "line break in an html block": ("<div>α<br>β</div>", "α\nβ"),
    "empty settings": ("---\n\n---\n# Τίτλος", "# Τίτλος"),
    "settings and a line further down": ("---\ntitle: x\n---\nα\n\n---\n\nβ", "title: x\n\nα\n\nβ"),
    "sentence that starts with an inner link": ("[Εισαγωγή](#intro) για αρχή.", "Εισαγωγή για αρχή."),
    "table of contents on one line": ("[α](#a) [β](#b)\n\nΚείμενο", "Κείμενο"),
    "inner link and a picture without alt text": ("[Εισαγωγή](#intro) ![](logo.png)", "Εισαγωγή"),
    "old mac line endings": ("---\rtitle: x\r---\r# Τίτλος\r\rα\rβ", "title: x\n\n# Τίτλος\n\nα\nβ"),
    "plain text": ("Απλό κείμενο χωρίς τίποτα.", "Απλό κείμενο χωρίς τίποτα."),
    "empty file": ("", ""),
}


# every part of markdown
@pytest.mark.parametrize("source, expected", MD_CASES.values(), ids=MD_CASES.keys())
def test_read_markdown(tmp_path, source, expected):
    assert read_md(tmp_path, source) == expected


# .md and .markdown point to read_markdown
@pytest.mark.parametrize("ext", [".md", ".markdown"])
def test_markdown_extensions_are_registered(ext):
    assert PARSERS.get(ext) is read_markdown


# a markdown file through parse_file
@pytest.mark.parametrize("name", ["notes.md", "README.MARKDOWN"])
def test_parse_file_reads_markdown(tmp_path, name):
    doc = parse_one(tmp_path, name, DOC_MD.encode())

    assert doc.parse_status == "ok"
    assert doc.content_type == "prose"
    assert doc.raw_text == EXPECTED_DOCX
