import pytest

from rag_fs.ingest.chunker import split_sections


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
