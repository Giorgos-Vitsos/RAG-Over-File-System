from pathlib import Path
from charset_normalizer import from_bytes

FALLBACK_ENCODINGS=["cp1253", "iso8859_7", "cp1252", "latin_1", "utf_16"]

def read_text(path: Path)->str:
    data=path.read_bytes()
    try:
        text=data.decode("utf-8-sig")#tries with utf-8 encoding then tries the most common in Greece
    except UnicodeDecodeError:
        best=from_bytes(data,cp_isolation=FALLBACK_ENCODINGS).best()#finds which one mathes the most
        if best is None:
            raise ValueError(f"{path} is not a text file")
        text=str(best)
    if "\x00" in text:#we check for NULL byte
        raise ValueError(f"{path} is not a text file")
    return text