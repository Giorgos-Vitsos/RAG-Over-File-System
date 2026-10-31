import re
from pathlib import Path
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from .tables import format_rows
from .text import clean_lines, read_text, unify_newlines, with_address

MARKDOWN=MarkdownIt("commonmark").enable("table").enable("strikethrough")#markdown reader 
HEADING_ANCHOR=re.compile(r"\s*\{#[^}]*\}\s*$")#google writes id after every heading this will be used to remove them
FRONT_MATTER=re.compile(r"\A---\n(.*?)\n---(?:\n|\Z)",re.S)#possible settings at the top of the file
BREAK_TAG=re.compile(r"<br\s*/?>",re.I)

def read_markdown(path:Path)->str:
    return markdown_text(read_text(path))

def markdown_text(text:str)->str:
    text=unify_newlines(text)
    blocks=[]
    front=FRONT_MATTER.match(text)#if we have settings on top
    if front:
        blocks.append(clean_lines(front.group(1)))#group(1) skips the start of the regex
        text=text[front.end():]#we keep only the rest
    blocks.extend(md_blocks(MARKDOWN.parse(text)))
    final_text=[]
    for block in blocks:
        if block:
            final_text.append(block)
    return "\n\n".join(final_text)

def md_blocks(tokens)->list[str]:
    blocks=[]
    index=0
    while index<len(tokens):
        current_token=tokens[index]
        if current_token.type=="heading_open":
            images=[]
            text=clean_lines(md_inline(tokens[index+1],images)).replace("\n"," ")#the heading is the next token and we make it a single line
            text=HEADING_ANCHOR.sub("",text)#we clean the id if it exists
            blocks.extend(images)
            if text:
                heading="#"*int(current_token.tag[1])+" "+text
                blocks.append(heading)
            index+=3 # open->line->close
            continue
        if current_token.type=="table_open":
            rows=[]
            while current_token.type!="table_close":
                if current_token.type=="tr_open":#we create the row   
                    rows.append([])
                elif current_token.type=="inline":
                    images=[]
                    text=md_inline(current_token,images)
                    rows[-1].append("\n".join(images+[text]))#text becomes list because images is a list and we add the cell to the row
                index+=1
                current_token=tokens[index]
            text=format_rows(rows)
            if text:
                blocks.append(text)
        elif current_token.type in ("fence","code_block"):
            if current_token.content.strip():
                blocks.append(current_token.content.rstrip("\n"))
        elif current_token.type=="html_block":
            text=clean_lines(BeautifulSoup(BREAK_TAG.sub("\n",current_token.content),"html.parser").get_text())
            if text:
                blocks.append(text)
        elif current_token.type=="inline" and not is_content_table(current_token):
            images=[]
            text=clean_lines(md_inline(current_token,images))
            blocks.extend(images)
            if text:
                blocks.append(text)
        index+=1
    return blocks

def md_inline(token,images:list)->str:
    parts=[]
    links=[]
    for child in token.children or []:
        kind=child.type
        if kind in ("text","code_inline"):
            parts.append(child.content)
        elif kind in ("softbreak","hardbreak"):
            parts.append("\n")
        elif kind=="html_inline":
            if BREAK_TAG.fullmatch(child.content.strip()):
                parts.append("\n")
        elif kind=="image":
            alt=(child.content or str(child.attrs.get("title") or "")).strip()
            if alt:
                images.append(f"[Image: {alt}]")
        elif kind=="link_open":
            links.append((str(child.attrs.get("href") or ""),len(parts)))
        elif kind=="link_close" and links:
            address,start=links.pop()
            text="".join(parts[start:])#the text of the link
            parts[start:]=[with_address(text,address)]
    return "".join(parts)

def is_content_table(token)->bool:#content table has only links inside the page
    inside=None
    found_flag=False
    for child in token.children or []:
        if child.type=="link_open":#we are inside a link
            inside=str(child.attrs.get("href") or "")
            if not inside.startswith("#"):
                return False
            found_flag=True#inside link
        elif child.type=="link_close":
            inside=None
        elif inside is None and child.type in ("text","code_inline","image") and (child.content.strip() or child.type=="image"):#its normal visible data
            return False
    return found_flag
