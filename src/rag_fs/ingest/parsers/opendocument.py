import datetime
import re
from pathlib import Path
from odf import teletype
from odf.opendocument import load, OpenDocument
from odf.style import Style
from .tabular import fill_merged, format_rows, format_sheets

ODF_ROW_GROUPS=("table:table-header-rows","table:table-row-group","table:table-rows")
HEADING_NAME=re.compile(r"Heading_20_(\d+)")
ODF_SPACES=re.compile(r"[ \t\r\n]+")
ODF_SKIP={
    "office:annotation","office:annotation-end",
    "text:note",
    "text:tracked-changes",
    "text:table-of-content","text:illustration-index","text:table-index","text:object-index",
    "text:alphabetical-index","text:user-index",
    "text:sequence-decls","text:variable-decls","text:user-field-decls","text:dde-connection-decls","office:forms",
    "draw:object","draw:object-ole"
}

def read_odf(path: Path)->str:
    file=load(str(path))
    for node in file.body.childNodes:
        if node.tagName=="office:text":
            return read_odt(file,node)
        elif node.tagName=="office:presentation":
            return read_odp(file,node)
    raise ValueError("Not an ODF text document or presentation")

def read_odt(file:OpenDocument,text)->str:
    return "\n\n".join(read_blocks(text,find_heading_styles(file)))

def read_odp(file:OpenDocument,presentation)->str:
    raise NotImplementedError("presentations are not read yet")

def find_heading_styles(file:OpenDocument)->dict[str,int]:
    parents={}
    levels={}#the styles that say their level themselves
    for styles in (file.styles,file.automaticstyles):#all type of styles
        for style in styles.getElementsByType(Style):
            if style.getAttribute("family")!="paragraph":#a name is unique only inside its family
                continue
            name=style.getAttribute("name")
            parents[name]=style.getAttribute("parentstylename")#we save its parent or None
            level=style.getAttribute("defaultoutlinelevel")
            match=HEADING_NAME.fullmatch(name)
            if name=="Title":
                levels[name]=1
            elif level:
                levels[name]=int(level)
            elif match:
                levels[name]=int(match.group(1))
    headings={}
    for name in parents:
        current=name
        seen=set()#saves us from infinite loops
        while current and current not in seen:#we go up the parents until a style with a level
            if current in levels:
                headings[name]=levels[current]
                break
            seen.add(current)
            current=parents.get(current)
    return headings

def read_blocks(node,headings:dict[str,int] | None)->list[str]:
    blocks=[]
    for child in node.childNodes:
        blocks.extend(gather_data(child,headings))
    return blocks

def gather_data(node,headings:dict[str,int] | None)->list[str]:
    tag=node.tagName
    if tag=="Text" or tag in ODF_SKIP:#text between blocks is only noise
        return []
    blocks=[]
    if tag in ("text:p","text:h"):
        shapes=[]#alt data like images
        text=get_paragraph_text(node,shapes)
        for shape in shapes:
            blocks.extend(gather_data(shape,headings))
        if not text:#if no other text we return only the alt
            return blocks
        level=0
        if headings is not None:
            if tag=="text:h":
                level=max(1,int(node.getAttribute("outlinelevel") or 1))#the heading level is always 1+
            else:
                level=headings.get(node.getAttribute("stylename"),0)
        if level>0:
            text="#"*level+" "+text
        blocks.append(text)
        return blocks
    if tag=="table:table":
        rows,merges,header_rows=odf_table_rows(node)
        text=format_rows(fill_merged(rows,merges),header_rows)
        if text:
            blocks.append(text)
        return blocks
    if tag=="draw:frame":
        alt=odf_image_alt(node)
        if alt:
            blocks.append(f"[Image: {alt}]")
    blocks.extend(read_blocks(node,headings))#group blocks
    return blocks

def get_paragraph_text(node,shapes:list)->str:
    lines=[]
    for line in odf_inline(node,shapes).split("\n"):#we clean every line of the paragraph
        line=line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def odf_inline(node,shapes:list)->str:
    parts=[]
    for child in node.childNodes:
        tag=child.tagName
        if tag=="Text":
            parts.append(ODF_SPACES.sub(" ",child.data))
        elif tag=="text:s":#repeation is marked in odf
            parts.append(" "*int(child.getAttribute("c") or 1))
        elif tag=="text:tab":
            parts.append("\t")
        elif tag=="text:line-break":
            parts.append("\n")
        elif tag.startswith("draw:"):
            shapes.append(child)
        elif tag not in ODF_SKIP:#wrappers are parsed recursivily
            parts.append(odf_inline(child,shapes))
    return "".join(parts)

def odf_image_alt(frame)->str:
    if not any(child.tagName=="draw:image" for child in frame.childNodes):#not a real image
        return ""
    alts={}
    for child in frame.childNodes:
        if child.tagName in ("svg:desc","svg:title"):
            alts[child.tagName]=teletype.extractText(child).strip()
    return alts.get("svg:desc") or alts.get("svg:title") or ""#we return the description if not empty else the title and then empty

def odf_row_list(node, rows: list, in_header: bool=False) -> list:
    for child in node.childNodes:
        if child.tagName=="table:table-row":
            rows.append((child,in_header))
        elif child.tagName in ODF_ROW_GROUPS:#rows can be inside groups
            odf_row_list(child,rows,in_header or child.tagName=="table:table-header-rows")
    return rows

def odf_cell_value(cell):
    kind=cell.getAttribute("valuetype")
    value=cell.getAttribute("value")
    date=cell.getAttribute("datevalue")
    if kind in ("float","percentage","currency") and value is not None:#we return the real value not how it is shown
        return float(value)
    if kind=="date" and date is not None:#we uniform the date
        return datetime.datetime.fromisoformat(date)
    return "\n".join(read_blocks(cell,None))

def odf_table_rows(table):
    rows=[]
    merges=[]
    header_rows=0
    blank_rows=0#empty rows we have not added yet
    for row,in_header in odf_row_list(table,[]):
        cells=[]
        blank_cells=0#empty cells we have not added yet
        for cell in row.childNodes:
            if cell.tagName not in ("table:table-cell","table:covered-table-cell"):
                continue
            repeat=int(cell.getAttribute("numbercolumnsrepeated") or 1)#how many times the cell repeats in this row
            value=""
            if cell.tagName=="table:table-cell":#only normal cells have value
                value=odf_cell_value(cell)
                down=int(cell.getAttribute("numberrowsspanned") or 1)
                across=int(cell.getAttribute("numbercolumnsspanned") or 1)
                if down>1 or across>1:#start of a merge
                    top=len(rows)+blank_rows
                    left=len(cells)+blank_cells
                    merges.append((top,left,top+down-1,left+across-1))
            if value=="":#if the merge is empty we might not need to add it we will find out later
                blank_cells+=repeat
                continue
            cells.extend([None]*blank_cells)#if we have data then add all the empties before to keep the structure correct
            blank_cells=0
            cells.extend([value]*repeat)
        repeat=int(row.getAttribute("numberrowsrepeated") or 1)
        if not cells:#if the row was empty we might not need to add it we will find out later
            blank_rows+=repeat
            continue
        for repeated_row in range(blank_rows):#if we have data we add the empty rows that repeat to keep the structure
            rows.append([])
        blank_rows=0
        for repeated_row in range(repeat):
            rows.append(list(cells))
        if in_header:
            header_rows+=repeat#repeating rows are all headers
    return rows,merges,header_rows

def read_ods(path: Path)->str:
    doc=load(str(path))
    content=[node for node in doc.body.childNodes if node.tagName=="office:spreadsheet"]
    if not content:#an odt or odp with the wrong extension
        raise ValueError("Not an .ods spreadsheet")
    sheets=[]
    for table in content[0].childNodes:
        if table.tagName=="table:table":
            rows,merges,header_rows=odf_table_rows(table)
            sheets.append((table.getAttribute("name"),fill_merged(rows,merges),header_rows))
    return format_sheets(sheets)
