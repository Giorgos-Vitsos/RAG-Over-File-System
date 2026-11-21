from rag_fs.models import Document
from rag_fs.ingest.parsers.text import GREEK,WORD,count_chars,word_share
import re

GREEK_PAIR=re.compile(f"[{GREEK}]{{2}}")#two greek letters in a row
LATIN_PAIR=re.compile("[A-Za-z]{2}")#two latin letters in a row
CONTROL_CHARS_RATIO=0.1
MIXED_WORDS_RATIO=0.01
FEW_WORDS=0.3
FEW_WORDS_MIN_TOKENS=20

def quality_reasons(doc: Document)->list[str]:
    reasons=[]
    mixed_count=0
    if doc.parse_status=="ocr":
        reasons.append("ocr")
    whitespace_count,control_char_count=count_chars(doc.raw_text)
    words=WORD.findall(doc.raw_text)
    for word in words:
        if GREEK_PAIR.search(word) and LATIN_PAIR.search(word):
            mixed_count+=1
    if control_char_count>(len(doc.raw_text)-whitespace_count)*CONTROL_CHARS_RATIO:
        reasons.append("control_chars")
    if "\ufffd" in doc.raw_text:
        reasons.append("replacement_chars")
    if mixed_count>len(words)*MIXED_WORDS_RATIO:
        reasons.append("mixed_words")
    tokens,share=word_share(doc.raw_text)
    if doc.content_type=="prose" and tokens>=FEW_WORDS_MIN_TOKENS and share<FEW_WORDS:
        reasons.append("few_words")
    return reasons
