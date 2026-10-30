from pathlib import Path
from charset_normalizer import from_bytes
import re
import unicodedata

FALLBACK_ENCODINGS=["cp1253", "iso8859_7", "cp1252", "latin_1", "utf_16"]
VALID_THRESHOLD=10
GREEK="\u0370-\u03ff\u1f00-\u1fff"#greek letters with the polytonic ones
WORD=re.compile(r"\w+")
GREEK_AFTER_PILCROW=re.compile(f"\u00b6[{GREEK}]")
ACCENT_START=re.compile(r"[\u0384\u00b4][ΑΕΗΙΟΥΩ]")#greek characters with accents that break
CP1252_SIGNS="".join(bytes([b]).decode("cp1252") for b in range(0x80,0xa0) if b not in (0x81,0x8d,0x8f,0x90,0x9d))
MOJIBAKE=re.compile("[\u0080-\u00ff"+re.escape(CP1252_SIGNS)+"]+")#possible broken characters

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

def unify_newlines(text:str)->str:
    return text.replace("\r\n","\n").replace("\r","\n")

def normalize(text:str)->str:
    text=fix_mojibake(text)
    text=ACCENT_START.sub(lambda match: unicodedata.normalize("NFC",match.group(0)[-1]+"\u0301"),text)
    text=unicodedata.normalize("NFKC",text)
    return text

def fix_mojibake(text:str)->str:
    return MOJIBAKE.sub(lambda match: fix_mojibake_run(match.group(0)),text)

def fix_mojibake_run(text:str)->str:
    data=[]
    for c in text:
        if ord(c)<=0xff:
            data.append(ord(c))
        else:
            data.append(c.encode("cp1252")[0])
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:#not a mojibake
        return text

def fix_suspicious_text(text:str)->str:#greek written in Windows-1253 but read as latin
    chars=[]
    for c in text:
        if not "\u00a0"<=c<="\u00ff":#its not broken
            chars.append(c)
            continue
        try:
            chars.append(bytes([ord(c)]).decode("cp1253"))
        except UnicodeDecodeError:#cp1253 does not have this
            try:
                chars.append(bytes([ord(c)]).decode("iso8859_7"))
            except UnicodeDecodeError:#iso8859_7 does not have this
                chars.append(c)
    return "".join(chars)

def is_greek(c: str) -> bool:
    return "\u0370" <= c <= "\u03ff" or "\u1f00" <= c <= "\u1fff"

def count_chars(text:str)->tuple[int,int]:#the spaces and the control characters
    whitespace=0
    control=0
    for c in text:
        if c.isspace():
            whitespace+=1
        elif unicodedata.category(c)=="Cc":
            control+=1
    return whitespace,control

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
