from pathlib import Path
from striprtf.striprtf import rtf_to_text
from .text import read_text

def read_rtf(path: Path)->str:
    text=read_text(path).strip()
    if not text.startswith(r"{\rtf1"):#if it doesnt start with that then its not a real .rtf file
        raise ValueError("Not an .rtf file")
    final=rtf_to_text(text).strip()
    return final
