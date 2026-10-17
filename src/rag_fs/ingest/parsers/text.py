from pathlib import Path
from charset_normalizer import from_bytes
import re

FALLBACK_ENCODINGS=["cp1253", "iso8859_7", "cp1252", "latin_1", "utf_16"]
VALID_THRESHOLD=10
GREEK_AFTER_PILCROW=re.compile("\u00b6[\u0370-\u03ff]")

def read_text(path: Path)->str:
    data=path.read_bytes()
    try:
        text=data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text=data.decode("utf-8-sig",errors="replace")#we replace the broken bytes
        broken_count=text.count("\ufffd")
        correct_count=0
        for c in text:
            if ord(c)>127 and c!="\ufffd": #above 127 we have the weird characters in ascii like greek etc.
                correct_count+=1
        if correct_count<VALID_THRESHOLD*broken_count:#if most characters are correct then thats probably the corrent encoding
            best=from_bytes(data,cp_isolation=FALLBACK_ENCODINGS).best()#tries the most common encodings in Greece
            if best is None:
                raise ValueError(f"{path} is not a text file")
            text=str(best)
            if best.encoding=="cp1253" and GREEK_AFTER_PILCROW.search(text):#edge case where ¶ was replacing capital Α
                text=data.decode("iso8859_7",errors="replace")
    if "\x00" in text:#we check for NULL byte
        raise ValueError(f"{path} is not a text file")
    return text

def clean_lines(text:str)->str:
    lines=[]
    empty=False
    for line in text.split("\n"):
        line=line.strip()
        if not line:
            empty=True
            continue
        if empty and lines:#we delete multiple empty lines
            lines.append("")
        lines.append(line)
        empty=False
    return "\n".join(lines)

def clean_address(address:str)->str:
    address=address.lower().strip()
    for prefix in ("mailto:","https://","http://","www."):
        address=address.removeprefix(prefix)
    return address.rstrip("/")

def with_address(text:str,address:str)->str:
    if not address or address.startswith("#") or not text.strip():
        return text
    if clean_address(text)==clean_address(address):
        return text
    core=text.rstrip()#we remove the end spaces so we can add the link
    return f"{core} ({address})"+text[len(core):]#we re-introduce the spaces