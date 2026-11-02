import re
from dataclasses import dataclass,field,replace
from pathlib import Path
from .office import field_address,heading_level
from .tables import format_rows
from .text import clean_lines,with_address

RTF_TOKENS=re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+",re.S)
SKIP={"stylesheet", "colortbl", "info", "pict", "object", "header", "headerl", "headerr", "headerf", 
      "footer", "footerl", "footerr", "footerf", "footnote", "pntext", "listtext", "pn", "xe", "tc"}
COMMAND_CHARS={#commands that are a character
    "line":"\n","tab":"\t","emdash":chr(0x2014),"endash":chr(0x2013),"bullet":chr(0x2022),"lquote":chr(0x2018),
    "rquote":chr(0x2019),"ldblquote":chr(0x201c),"rdblquote":chr(0x201d),"emspace":" ","enspace":" ",
}
SYMBOL_CHARS={#a \ and one character that is a character
    "~":chr(0xa0),"_":"-","-":"","{":"{","}":"}","\\":"\\"
}
CHARSETS={161:"cp1253",162:"cp1254",204:"cp1251",238:"cp1250",177:"cp1255",178:"cp1256",186:"cp1257"}

@dataclass
class State:
    skip: bool=False
    first_command: bool=False
    star: bool=False
    uc: int=1#num of chars following \u
    font_table: bool=False
    font:int|None=None
    field: bool=False
    instruction: bool=False
    
def read_rtf(path: Path)->str:
    data=path.read_bytes()
    file_text=data.decode("latin-1")
    file_text=file_text.lstrip()
    if not file_text.startswith(r"{\rtf1"):#if it doesnt start with that then its not a real .rtf file
        raise ValueError("Not an .rtf file")
    paragraphs=[]
    parts=[]
    groups=[State()]
    skip_chars=0
    page_code="cp1252"
    fonts={}
    font_def=None
    fields=[]
    table={"in_table":False,"cell_lines":[],"cells":[],"rows":[],"header_rows":0,"row_header":False,"merge_flags":[],"merge_flag":""}#dic that holds every table relationship
    for match in RTF_TOKENS.finditer(file_text):
        command=match.group(1)
        number=match.group(2)
        hex_byte=match.group(3)
        symbol=match.group(4)
        brace=match.group(5)
        text=match.group(6)
        curr_group=groups[-1]
        if brace=="{":#starts a group
            new_group=replace(curr_group, first_command=True, star=False,field=False)#those are not inhereted
            groups.append(new_group)
        elif brace=="}":#ends a group
            if len(groups)>1:
                closed=groups.pop()
                if closed.field and fields:#if we closed a field
                    end_field(parts,fields.pop())
                    
        elif command is not None:
            if curr_group.first_command:
                if command=="fonttbl":
                    curr_group.font_table=True
                elif command=="field":
                    curr_group.field=True
                    fields.append({"code":"","start":None})
                elif command=="fldinst":
                    curr_group.instruction=True
                elif command=="fldrslt":#field result
                    if fields:
                        fields[-1]["start"]=len(parts)#where the result starts
                elif command in SKIP or curr_group.star:
                    curr_group.skip=True
            curr_group.first_command=False
            if curr_group.skip:
                continue
            if curr_group.font_table:
                if command=="f":
                    if number is not None:
                        font_def=int(number)
                elif command=="fcharset" and number is not None and font_def is not None and int(number) in CHARSETS:
                    fonts[font_def]=CHARSETS[int(number)]
                continue
            if skip_chars>0:#command counts as char
                skip_chars-=1
                continue
            if command=="par":#end of a paragraph
                add_paragraph(parts,paragraphs,table)
            elif command=="uc":
                if number is not None:
                    curr_group.uc=int(number)
                else:
                    curr_group.uc=1
            elif command=="u":
                if number is not None:
                    code=int(number)
                    if code<0:#its 16 bit so some numbers are written negative
                        code+=65536
                    parts.append(chr(code))
                    skip_chars=curr_group.uc
            elif command=="ansicpg" and number is not None:
                page_code=f"cp{number}"
            elif command=="f" and number is not None:
                curr_group.font=int(number)
            elif command in COMMAND_CHARS:
                parts.append(COMMAND_CHARS[command])
            else:
                table_command(command,parts,table)
        elif symbol=="*":
            curr_group.star=True
        elif symbol is not None:
            curr_group.first_command=False
            if curr_group.skip or curr_group.font_table:
                continue
            if curr_group.instruction:
                if fields and symbol in SYMBOL_CHARS:#we fix rtf instruction symbols
                    fields[-1]["code"]+=SYMBOL_CHARS[symbol]
                continue
            if symbol=="\n" or symbol=="\r":
                add_paragraph(parts,paragraphs,table)
            elif symbol in SYMBOL_CHARS:
                parts.append(SYMBOL_CHARS[symbol])
        elif hex_byte is not None:
            curr_group.first_command=False
            if curr_group.skip or curr_group.font_table:
                continue
            if skip_chars>0:#byte counts as a char to be skipped
                skip_chars-=1
            else:
                parts.append(decode(chr(int(hex_byte,16)),curr_group.font,fonts,page_code))
        elif text is not None:
            curr_group.first_command=False
            if curr_group.skip or curr_group.font_table:
                continue
            if skip_chars>0:
                skipped=min(skip_chars,len(text))
                skip_chars-=skipped
                text=text[skipped:]
            if text:
                text=decode(text,curr_group.font,fonts,page_code)
                if curr_group.instruction:
                    if fields:
                        fields[-1]["code"]+=text
                else:
                    parts.append(text)
    add_paragraph(parts,paragraphs,table)#the last paragraph ends without "par"
    end_table(paragraphs,table)
    return "\n\n".join(paragraphs) 
    
def add_paragraph(parts:list[str],paragraphs:list[str],table:dict)->None:
    text=clean_lines("".join(parts))
    parts.clear()
    if table["in_table"]:
        table["cell_lines"].append(text)
    else:
        end_table(paragraphs,table)#par outside table ends all above tables
        if text:
            paragraphs.append(text)
    

def decode(text:str,font:int|None,fonts:dict[int,str],code_page:str)->str:
    if font is not None and font in fonts:
        code_page=fonts[font]
    try:
        return text.encode("latin-1").decode(code_page,errors="replace")
    except LookupError:
        return text

def end_field(parts:list[str],current:dict)->None:#the result of a link gets its address
    start=current["start"]
    if start is None:#no result
        return
    start=min(start,len(parts))#to be safe if parts where emptied before hand
    result="".join(parts[start:])
    parts[start:]=[with_address(result,field_address(current["code"]))]#we connect links with text

def table_command(command:str,parts:list[str],table:dict)->None:
    if command=="pard":#we leave the table
        table["in_table"]=False
    elif command=="intbl":#we enter the table
        table["in_table"]=True
    elif command=="cell":#end of a cell
        end_cell(parts,table)
    elif command=="row":
        if parts:
            end_cell(parts,table)
        end_row(table)
    elif command=="trowd":#in new line we have diffrent settings
        table["merge_flags"]=[]
        table["merge_flag"]=""
        table["row_header"]=False
    elif command=="trhdr":#header row
        table["row_header"]=True
    elif command=="clvmgf":#merged down
        table["merge_flag"]="first"
    elif command=="clvmrg":#merged up
        table["merge_flag"]="continue"
    elif command=="cellx":#end of the settings of a cell
        table["merge_flags"].append(table["merge_flag"])
        table["merge_flag"]=""

def end_cell(parts:list[str],table:dict)->None:
    table["cell_lines"].append(clean_lines("".join(parts)))#we add the current part to the current cell lines
    parts.clear()
    lines=[]
    for line in table["cell_lines"]:
        if line:
            lines.append(line)
    table["cells"].append("\n".join(lines))
    table["cell_lines"]=[]

def end_row(table:dict)->None:
    cells=table["cells"]
    rows=table["rows"]
    for index,flag in enumerate(table["merge_flags"]):
        if flag=="continue" and rows and index<len(cells) and index<len(rows[-1]):#merge
            cells[index]=rows[-1][index]#the cell is the one above
    if table["row_header"] and table["header_rows"]==len(rows):#row is header if we are still in header region
        table["header_rows"]+=1
    rows.append(cells)
    table["cells"]=[]#a new list, the old one is now a row

def end_table(paragraphs:list[str],table:dict)->None:
    if table["cells"]:#forgotten cells become a row
        end_row(table)
    if table["rows"]:
        text=format_rows(table["rows"],table["header_rows"])
        if text:
            paragraphs.append(text)
    table["rows"]=[]#clean up for next table
    table["header_rows"]=0