import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from .markdown import markdown_text
from .tables import fill_merged,format_rows
from .text import clean_lines,read_text,with_address

HTML_SKIP={"head","script","style","noscript","template","svg"}
HTML_INLINE={"a","abbr","b","bdi","bdo","br","cite","code","data","del","dfn","em","font","i","img","ins","kbd","label",
             "mark","q","s","samp","small","span","strong","sub","sup","time","u","var","wbr"}
HEADINGS={"h1":1,"h2":2,"h3":3,"h4":4,"h5":5,"h6":6}
HTML_SPACES=re.compile(r"[ \t\r\n\f]+")
MAX_SPAN=1000

def read_html(path:Path)->str:
    return html_text(read_text(path))

def html_text(markup:str)->str:
    return "\n\n".join(html_blocks(BeautifulSoup(markup,"html.parser")))

def html_blocks(node)->list[str]:
    blocks=[]
    parts=[]#text of the current paragraph
    images=[]
    for child in node.children:
        if not isinstance(child,Tag) or child.name in HTML_INLINE:#normal data
            parts.append(html_inline(child,images))
            continue
        if child.name in HTML_SKIP:
            continue
        add_paragraph(parts,images,blocks)#we get here only in block start/end, either way we finish last block (if it exists)
        if child.name in HEADINGS:
            text=clean_lines(inline_text(child,images)).replace("\n"," ")
            blocks.extend(images)
            images.clear()#stops double images
            if text:
                blocks.append("#"*HEADINGS[child.name]+" "+text)
        elif child.name=="table":
            rows,header_rows=html_table(child)
            text=format_rows(rows,header_rows)
            if text:
                blocks.append(text)
        elif child.name=="pre":#preformatted text
            text=child.get_text().strip("\n")
            if text.strip():
                blocks.append(text)
        else:#wrappers 
            blocks.extend(html_blocks(child))
    add_paragraph(parts,images,blocks)#we end the last paragraph
    return blocks

def add_paragraph(parts:list[str],images:list[str],blocks:list[str])->None:
    text=clean_lines("".join(parts))
    blocks.extend(images)
    if text:#we add the paragph data
        blocks.append(text)
    parts.clear()#and then clean for next usage
    images.clear()

def html_inline(node,images:list[str])->str:
    if not isinstance(node,Tag):
        if type(node) is not NavigableString:#all of them must be at least children of 
            return ""
        return HTML_SPACES.sub(" ",str(node))#fixes spaces
    if node.name in HTML_SKIP:
        return ""
    if node.name=="br":
        return "\n"
    if node.name=="img":
        alt=str(node.get("alt") or "").strip()
        if alt:
            images.append(f"[Image: {alt}]")
        return ""
    text=inline_text(node,images)
    if node.name=="a":
        return with_address(text,str(node.get("href") or ""))
    return text

def inline_text(node:Tag,images:list[str])->str:
    parts=[]
    for child in node.children:
        parts.append(html_inline(child,images))
    return "".join(parts)

def html_table(table:Tag)->tuple[list[list[str|None]],int]:#second num is the header
    rows_tags=[]
    for tr in table.find_all("tr"):
        if isinstance(tr,Tag) and tr.find_parent("table") is table:#we only get the currrent table
            rows_tags.append(tr)
    rows=[]
    merges=[]
    taken=set()#cells covered by a cell of a row above
    header_rows=0
    for tr in rows_tags:
        cells=[]
        all_th=True
        for cell in tr.find_all(["td","th"],recursive=False):#we look at only close children
            if not isinstance(cell,Tag):
                continue
            while (len(rows),len(cells)) in taken:#if its taken
                cells.append(None)
            top,left=len(rows),len(cells)#cell start
            across=span(cell,"colspan")#columns it spans
            down=min(span(cell,"rowspan"),len(rows_tags)-top)#rows it spans (cant go below last row)
            cells.append("\n".join(html_blocks(cell)))
            cells.extend([None]*(across-1))#merged cell
            if across>1 or down>1:
                merges.append((top,left,top+down-1,left+across-1))
                for r in range(top+1,top+down):
                    for c in range(left,left+across):
                        taken.add((r,c))#we mark that below the merged is taken
            if cell.name!="th":
                all_th=False
        while (len(rows),len(cells)) in taken:#covered cells at the end of the row
            cells.append(None)
        if header_rows==len(rows) and cells and (all_th or tr.find_parent("thead") is not None):#the header is the rows at the top
            header_rows+=1
        rows.append(cells)
    return fill_merged(rows,merges),header_rows

def span(cell:Tag,name:str)->int:
    try:
        value=int(str(cell.get(name) or 1))
    except ValueError:#if it doesnt have that attribute then span is 1
        return 1
    return max(1,min(value,MAX_SPAN))#at least 1

def read_xml(path:Path)->str:
    lines=[]
    xml_lines(ET.parse(path).getroot(),lines)
    return "\n".join(lines)

def xml_lines(node:ET.Element,lines:list[str])->None:
    own_text=(node.text or "").strip() or any((child.tail or "").strip() for child in node)#it has text or it has text inside of it
    if own_text:
        lines.append(" ".join("".join(node.itertext()).split()))
        return
    for child in node:#its a wrapper
        xml_lines(child,lines)

def read_notebook(path:Path)->str:
    notebook=json.loads(read_text(path))
    if not isinstance(notebook,dict) or not isinstance(notebook.get("cells"),list):
        raise ValueError(f"{path} is not a notebook")
    blocks=[]
    for cell in notebook["cells"]:
        blocks.extend(cell_blocks(cell))
    return "\n\n".join(blocks)

def cell_blocks(cell:dict)->list[str]:
    source=joined(cell.get("source"))
    kind=cell.get("cell_type")#type of notebook cell
    if kind=="markdown":
        text=markdown_text(source)
    elif kind=="code":
        text=source.strip("\n")#code keeps its spaces
    else:
        text=source.strip()
    blocks=[]
    if text.strip():
        blocks.append(text)
    for output in cell.get("outputs") or []:#output of a runned cell
        text=output_text(output)
        if text.strip():
            blocks.append(text)
    return blocks

def output_text(output:dict)->str:
    kind=output.get("output_type")
    if kind=="stream":
        if output.get("name")!="stdout":#stderr has warnings
            return ""
        return joined(output.get("text")).strip("\n")
    if kind not in ("execute_result","display_data"):#errors
        return ""
    data=output.get("data") or {}
    if any(key.startswith("image/") for key in data):
        return ""
    if "text/html" in data:
        return html_text(joined(data["text/html"]))
    return joined(data.get("text/plain")).strip("\n")

def joined(value)->str:#notebooks text can be multiple lines as list
    if isinstance(value,list):
        return "".join(str(line) for line in value)
    return str(value or "")
