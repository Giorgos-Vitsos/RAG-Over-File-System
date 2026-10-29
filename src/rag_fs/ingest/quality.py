from rag_fs.models import Document
import unicodedata
import re

WORD = re.compile(r"\w+")
GREEK_PAIR=re.compile("[\u0370-\u03ff\u1f00-\u1fff]{2}")#two greek letters in a row
LATIN_PAIR=re.compile("[A-Za-z]{2}")#two latin letters in a row
CONTROL_CHARS_RATIO=0.1
MIXED_WORDS_RATIO=0.01

def quality_reasons(doc: Document)->list[str]:
    reasons=[]
    whitespace_count=0
    control_char_count=0
    replacement_flag=False
    mixed_count=0
    if doc.parse_status=="ocr":
        reasons.append("ocr")
    for c in doc.raw_text:
        if c.isspace():
            whitespace_count+=1
        elif unicodedata.category(c)=="Cc":
            control_char_count+=1
        if c=="\ufffd":
            replacement_flag=True
    words=WORD.findall(doc.raw_text)
    for word in words:
        if GREEK_PAIR.search(word) and LATIN_PAIR.search(word):
            mixed_count+=1
    if control_char_count>(len(doc.raw_text)-whitespace_count)*CONTROL_CHARS_RATIO:
        reasons.append("control_chars")
    if replacement_flag:
        reasons.append("replacement_chars")
    if mixed_count>len(words)*MIXED_WORDS_RATIO:
        reasons.append("mixed_words")
    return reasons