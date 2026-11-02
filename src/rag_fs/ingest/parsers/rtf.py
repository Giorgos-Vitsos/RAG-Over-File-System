import re
from dataclasses import dataclass,field,replace
from pathlib import Path
from .office import field_address,heading_level
from .tables import format_rows
from .text import clean_lines,with_address

RTF_TOKENS=re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+",re.S)
SKIP={"colortbl", "info", "pict", "object", "header", "headerl", "headerr", "headerf",
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
    style_sheet: bool=False

@dataclass
class RtfReader:
    paragraphs:list[str]=field(default_factory=list)
    parts:list[str]=field(default_factory=list)#text of the current paragraph
    groups:list[State]=field(default_factory=lambda:[State()])
    skip_chars:int=0#chars after a \u that we still skip
    page_code:str="cp1252"
    fonts:dict[int,str]=field(default_factory=dict)#font number to its code page
    font_def:int|None=None#the font the font table defines now
    fields:list[dict]=field(default_factory=list)#the fields (links) that are open
    table:dict=field(default_factory=lambda:{"in_table":False,"cell_lines":[],"cells":[],"rows":[],"header_rows":0,
                                               "row_header":False,"merge_flags":[],"merge_flag":""})#dic that holds every table relationship
    styles:dict[int,str]=field(default_factory=dict)#style number to its name
    style_def:int=0#the style that we define now
    style_name:list[str]=field(default_factory=list)
    style:int=0#the style of the paragraph
    outline:int|None=None#the level the paragraph says itself

def read_rtf(path: Path)->str:
    data=path.read_bytes()
    file_text=data.decode("latin-1")
    file_text=file_text.lstrip()
    if not file_text.startswith(r"{\rtf1"):#if it doesnt start with that then its not a real .rtf file
        raise ValueError("Not an .rtf file")
    reader=RtfReader()
    for match in RTF_TOKENS.finditer(file_text):
        command,number,hex_byte,symbol,brace,text=match.groups()
        if brace=="{":
            open_group(reader)
        elif brace=="}":
            close_group(reader)
        elif command is not None:
            control_word(reader,command,int(number) if number is not None else None)
        elif symbol=="*":#the next command names the group
            reader.groups[-1].star=True
        elif symbol is not None:
            control_symbol(reader,symbol)
        elif hex_byte is not None:
            byte_char(reader,hex_byte)
        elif text is not None:
            text_chunk(reader,text)
    end_paragraph(reader)#the last paragraph ends without "par"
    end_table(reader.paragraphs,reader.table)
    return "\n\n".join(reader.paragraphs)

def open_group(reader:RtfReader)->None:#starts a group
    curr_group=reader.groups[-1]
    if curr_group.style_sheet:#each style is a group of its own
        reader.style_def=0
        reader.style_name=[]
    reader.groups.append(replace(curr_group, first_command=True, star=False,field=False))#those are not inhereted

def close_group(reader:RtfReader)->None:#ends a group
    if len(reader.groups)==1:
        return
    closed=reader.groups.pop()
    if closed.field and reader.fields:#if we closed a field
        end_field(reader.parts,reader.fields.pop())
    elif closed.style_sheet and reader.groups[-1].style_sheet:#a style of the style sheet ends
        end_style(reader.styles,reader.style_def,reader.style_name)

def control_word(reader:RtfReader,command:str,number:int|None)->None:
    curr_group=reader.groups[-1]
    if curr_group.first_command:
        group_command(reader,curr_group,command)
    curr_group.first_command=False
    if curr_group.skip:
        return
    if curr_group.font_table:
        font_table_command(reader,command,number)
    elif curr_group.style_sheet:#the style sheet only says the name of each style
        if command=="s" and number is not None:
            reader.style_def=number
    elif reader.skip_chars>0:#command counts as char
        reader.skip_chars-=1
    else:
        body_command(reader,curr_group,command,number)

def group_command(reader:RtfReader,curr_group:State,command:str)->None:#the first command of a group can say what the group is
    if command=="fonttbl":
        curr_group.font_table=True
    elif command=="stylesheet":
        curr_group.style_sheet=True
    elif command=="field":
        curr_group.field=True
        reader.fields.append({"code":"","start":None})
    elif command=="fldinst":
        curr_group.instruction=True
    elif command=="fldrslt":#field result
        if reader.fields:
            reader.fields[-1]["start"]=len(reader.parts)#where the result starts
    elif command in SKIP or curr_group.star:
        curr_group.skip=True

def font_table_command(reader:RtfReader,command:str,number:int|None)->None:
    if command=="f" and number is not None:
        reader.font_def=number
    elif command=="fcharset" and number is not None and reader.font_def is not None and number in CHARSETS:
        reader.fonts[reader.font_def]=CHARSETS[number]

def body_command(reader:RtfReader,curr_group:State,command:str,number:int|None)->None:#a command of the text of the document
    if command=="par":#end of a paragraph
        end_paragraph(reader)
    elif command=="uc":
        if number is not None:
            curr_group.uc=number
        else:
            curr_group.uc=1
    elif command=="u":
        if number is not None:
            if number<0:#its 16 bit so some numbers are written negative
                number+=65536
            add_text(reader,chr(number))
            reader.skip_chars=curr_group.uc
    elif command=="ansicpg" and number is not None:
        reader.page_code=f"cp{number}"
    elif command=="f" and number is not None:
        curr_group.font=number
    elif command=="pard":#a new paragraph starts with the default settings
        reader.table["in_table"]=False
        reader.style=0
        reader.outline=None
    elif command=="s" and number is not None:
        reader.style=number
    elif command=="outlinelevel" and number is not None:
        reader.outline=number
    elif command in COMMAND_CHARS:
        add_text(reader,COMMAND_CHARS[command])
    else:
        table_command(command,reader.parts,reader.table)

def control_symbol(reader:RtfReader,symbol:str)->None:
    curr_group=reader.groups[-1]
    curr_group.first_command=False
    if symbol=="\n" or symbol=="\r":#a \ at the end of a line is the end of a paragraph of the text
        if not (curr_group.skip or curr_group.font_table or curr_group.style_sheet or curr_group.instruction):
            end_paragraph(reader)
    elif symbol in SYMBOL_CHARS:
        add_text(reader,SYMBOL_CHARS[symbol])

def byte_char(reader:RtfReader,hex_byte:str)->None:
    curr_group=reader.groups[-1]
    curr_group.first_command=False
    if curr_group.skip or curr_group.font_table:
        return
    if reader.skip_chars>0:#byte counts as a char to be skipped
        reader.skip_chars-=1
        return
    add_text(reader,decode(chr(int(hex_byte,16)),curr_group.font,reader.fonts,reader.page_code))

def text_chunk(reader:RtfReader,text:str)->None:
    curr_group=reader.groups[-1]
    curr_group.first_command=False
    if curr_group.skip or curr_group.font_table:
        return
    if reader.skip_chars>0:
        skipped=min(reader.skip_chars,len(text))
        reader.skip_chars-=skipped
        text=text[skipped:]
    if text:
        add_text(reader,decode(text,curr_group.font,reader.fonts,reader.page_code))

def add_text(reader:RtfReader,text:str)->None:
    curr_group=reader.groups[-1]
    if curr_group.skip or curr_group.font_table:
        return
    if curr_group.style_sheet:#text is the name of the style
        reader.style_name.append(text)
    elif curr_group.instruction:
        if reader.fields:
            reader.fields[-1]["code"]+=text
    else:
        reader.parts.append(text)

def end_paragraph(reader:RtfReader)->None:
    add_paragraph(reader.parts,reader.paragraphs,reader.table,paragraph_level(reader.styles,reader.style,reader.outline))

def add_paragraph(parts:list[str],paragraphs:list[str],table:dict,level:int)->None:
    text=clean_lines("".join(parts))
    parts.clear()
    if table["in_table"]:
        table["cell_lines"].append(text)
    else:
        end_table(paragraphs,table)#par outside table ends all above tables
        if text:
            if level>0:#a heading is one line with # for its level
                text="#"*level+" "+text.replace("\n"," ")
            paragraphs.append(text)

def paragraph_level(styles:dict[int,str],style:int,outline:int|None)->int:#0 for normal text
    if outline is not None:#the paragraph says its level itself, 9 is normal text
        if outline<9:
            return outline+1
        return 0
    name=styles.get(style,"").split(",")[0].strip()#a style can have more names after a comma
    return heading_level(name.title())#heading 1 -> Heading 1 like in docx

def end_style(styles:dict[int,str],style_def:int,style_name:list[str])->None:
    name="".join(style_name).strip().rstrip(";").strip()#the name ends with ;
    if name:
        styles[style_def]=name
    

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
    if command=="intbl":#we enter the table
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