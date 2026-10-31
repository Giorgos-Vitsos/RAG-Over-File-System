import re
from dataclasses import dataclass,field,replace
from pathlib import Path
from .office import field_address,heading_level
from .tables import format_rows
from .text import clean_lines,with_address

RTF_TOKENS=re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+",re.S)
SKIP={"fonttbl", "stylesheet", "colortbl", "info", "pict", "object", "header", "headerl", "headerr", "headerf", 
      "footer", "footerl", "footerr", "footerf", "footnote", "pntext", "listtext", "pn", "xe", "tc"}

@dataclass
class State:
    skip: bool=False
    first_command: bool=False
    star: bool=False

def read_rtf(path: Path)->str:
    data=path.read_bytes()
    file_text=data.decode("latin-1")
    file_text=file_text.lstrip()
    if not file_text.startswith(r"{\rtf1"):#if it doesnt start with that then its not a real .rtf file
        raise ValueError("Not an .rtf file")
    paragraphs=[]
    parts=[]
    groups=[State()]
    for match in RTF_TOKENS.finditer(file_text):
        command=match.group(1)
        symbol=match.group(4)
        brace=match.group(5)
        text=match.group(6)
        curr_group=groups[-1]
        if brace=="{":#starts a group
            new_group=replace(curr_group, first=True, star=False)
            groups.append(new_group)
        elif brace=="}":#ends a group
            if len(groups)>1:
                groups.pop()
        elif command is not None:
            if curr_group.first_command and (command in SKIP or curr_group.star):#if first command tells us to skip
                curr_group.skip=True
            curr_group.first_command=False
            if curr_group.skip:
                continue
            if command=="par":
                add_paragraph(parts,paragraphs)
        elif symbol=="*":
            curr_group.star=True
        elif text is not None:
            curr_group.first_command=False
            if not curr_group.skip:
                parts.append(text)
    add_paragraph(parts,paragraphs)
    return "\n\n".join(paragraphs) 
    
def add_paragraph(parts:list[str],paragraphs:list[str])->None:
    text=clean_lines("".join(parts))
    if text:
        paragraphs.append(text)
    parts.clear()