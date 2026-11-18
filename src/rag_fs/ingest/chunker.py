import re
from rag_fs.models import Document,Chunk
from rag_fs.config import ChunkingConfig
from pathlib import Path
import tree_sitter_language_pack as grammars
from tree_sitter import Node,Parser

HEADING=re.compile(r"(#{1,6}) (.*)")
FENCE="```"#code block
WORD=re.compile(r"\S+")
PARSERS:dict[str,Parser|None]={}
GRAMMARS:dict[str,str|None]={"folder":None}

def trimmed(text:str,start:int,end:int)->tuple[int,int]:#removes whitespace and gets a part of the raw text
    while start<end and text[start].isspace():
        start+=1
    while end>start and text[end-1].isspace():
        end-=1
    return start,end

def add_section(sections:list[tuple[int,int,str]],text:str,start:int,end:int,title:str)->None:
    start,end=trimmed(text,start,end)
    if start<end:
        sections.append((start,end,title))

def split_sections(text:str)->list[tuple[int,int,str]]:
    sections=[]
    trail=[]#the info of the above section
    start=0
    title=""
    position=0
    inside_code=False
    for line in text.split("\n"):
        if inside_code:
            if line==FENCE:#we need to close ```
                inside_code=False
        elif line.startswith(FENCE):#or start ```
            inside_code=True
        else:
            match=HEADING.fullmatch(line)
            if match and match.group(2).strip():
                add_section(sections,text,start,position,title)#new heading closes the last section
                level=len(match.group(1))
                while trail and trail[-1][0]>=level:
                    trail.pop()
                trail.append((level,match.group(2).strip()))
                title=" > ".join(name for _,name in trail)
                start=position
        position+=len(line)+1
    add_section(sections,text,start,len(text),title)
    return sections

def split_windows(text, start, end, words:int, overlap) -> list[tuple[int,int]]:
    if words<1 or overlap<0:
        raise ValueError(f"Values words and overlap should not be negative, current: {words}, {overlap}")
    step=words-round(words*overlap)
    if step<1:
        raise ValueError(f"Chunking step has invalid value of {step}, the overlap {overlap} consumes the whole window")
    found_words=[]
    for match in WORD.finditer(text,start,end):
        found_words.append((match.start(),match.end()))
    windows=[]
    first_index=0
    while first_index<len(found_words):
        last=min(first_index+words,len(found_words))-1#in case the last chunk doesnt have enough words
        windows.append((found_words[first_index][0],found_words[last][1]))
        if first_index+words>=len(found_words):#last word
            break
        first_index+=step
    return windows

def make_chunk(doc:Document,start,end,title,strategy:str)->Chunk:
    file_path_txt=doc.file_path.as_posix()
    text=doc.raw_text[start:end]
    index_text=f"File: {file_path_txt}\n"
    if title:
        index_text+=f"Section: {title}\n"
    index_text+="\n"+text
    chunk_id=f"{file_path_txt}#{start}-{end}"
    return Chunk(chunk_id=chunk_id,file_path=doc.file_path,char_start=start,char_end=end,text=text,index_text=index_text,content_type=doc.content_type,chunk_strategy=strategy)

def chunk_doc(doc:Document,config:ChunkingConfig)->list[Chunk]:
    if not doc.raw_text.strip():
        return [make_chunk(doc,0,0,"",config.strategy)]
    if doc.content_type=="code":#code is never cut at headings: a comment like "# load" is not one (D112)
        use_grammars(config.grammars)
        language=code_language(doc.ext)
        code_pieces=split_code(doc.raw_text,language,config.narrative_words) if language else None
        if code_pieces is None:#no grammar: blocks between empty lines
            code_pieces=[(start,end,"") for start,end in split_blocks(doc.raw_text,0,len(doc.raw_text),config.narrative_words)]
        return [make_chunk(doc,start,end,title,config.strategy) for start,end,title in code_pieces]
    chunks=[]
    for start,end,title in split_sections(doc.raw_text):
        if doc.content_type=="tabular":#we cut tables
            pieces=split_rows(doc.raw_text,start,end,config.tabular_rows,config.narrative_words)
        else:
            pieces=split_windows(doc.raw_text,start,end,config.narrative_words,config.overlap)
        for piece_s,piece_e in pieces:
            chunks.append(make_chunk(doc,piece_s,piece_e,title,config.strategy))
    return chunks

def split_rows(text:str, start:int, end:int, rows:int, words:int) -> list[tuple[int,int]]:
    if rows<1 or words<1:
        raise ValueError(f"Split rows couldnt work because it needs values of at least 1, current: {rows}, {words}")
    groups=[]
    group_s=0
    group_e=0
    group_rows=0#
    group_words=0#
    position=start#line
    for line in text[start:end].split("\n"):
        line_s,line_e=trimmed(text,position,position+len(line))
        position+=len(line)+1
        if line_s==line_e:#empty line is not a row
            continue
        line_words=len(text[line_s:line_e].split())
        if group_rows>0 and (group_rows==rows or group_words+line_words>words):#full group we close it
            groups.append((group_s,group_e))
            group_rows=0
            group_words=0
        if group_rows==0:#new group starts with this line
            group_s=line_s
        group_e=line_e
        group_rows+=1
        group_words+=line_words
    if group_rows>0:#the last group
        groups.append((group_s,group_e))
    return groups

def code_blocks(text:str,start:int,end:int)->list[list[int]]:
    blocks=[]
    position=start
    prev_empty=True
    for line in text[start:end].split("\n"):
        line_s,line_e=trimmed(text,position,position+len(line))
        if line_s==line_e:
            prev_empty=True
        else:
            words=len(text[line_s:line_e].split())
            if prev_empty:#we start new block
                blocks.append([line_s,line_e,words,line_s-position])
            else:#we add to the last one
                blocks[-1][1]=line_e
                blocks[-1][2]+=words
            prev_empty=False
        position+=len(line)+1
    return blocks

def split_blocks(text:str,start:int,end:int,words:int)->list[tuple[int,int]]:
    if words<1:
        raise ValueError(f"In split_blocks words should be at least 1, current: {words}")
    pieces=[]
    group=[]
    for block in code_blocks(text,start,end):
        if block[2]>words:#bigger than limit
            if group:#we close the group if open
                pieces.append((group[0][0],group[-1][1]))
                group=[]
            pieces.extend(split_rows(text,block[0],block[1],words,words))
            continue
        while group and sum(b[2] for b in group)+block[2]>words:#out of limit
            starts=group[1:]+[block]#we can cut before any of these blocks
            least=min(b[3] for b in starts)
            cut=[b for b in starts if b[3]==least][-1]
            if cut is block:
                pieces.append((group[0][0],group[-1][1]))
                group=[]
            else:
                index=group.index(cut)
                pieces.append((group[0][0],group[index-1][1]))
                group=group[index:]
        group.append(block)
    if group:
        pieces.append((group[0][0],group[-1][1]))
    return pieces


def use_grammars(folder:str)->None:
    if GRAMMARS["folder"]!=folder:
        grammars.configure(grammars.PackConfig(cache_dir=folder))
        GRAMMARS["folder"]=folder
        PARSERS.clear()

def code_language(ext:str)->str|None:
    name=ext.lstrip(".").lower()
    if name=="h":#.h are read as cpp rather than c
        return "cpp"
    language=grammars.detect_language_from_extension(name)
    if language is None or not grammars.has_language(language):
        return None
    return language

def code_parser(language:str)->Parser|None:
    if language not in PARSERS:
        try:
            PARSERS[language]=grammars.get_parser(language)
        except Exception:#no grammar for this language goes to fallback
            PARSERS[language]=None
    return PARSERS[language]

def node_name(node:Node)->str|None:
    target=node.child_by_field_name("definition") or node#definitions for python code
    name=target.child_by_field_name("name")
    if name is None:
        name=target.child_by_field_name("declarator")#c and c++ keep the name inside declarators
        while name is not None and name.child_by_field_name("declarator") is not None:
            name=name.child_by_field_name("declarator")
    if name is None or name.text is None:
        return None
    return name.text.decode("utf-8","replace").strip() or None

def is_definition(node:Node)->bool:
    has_body=node.child_by_field_name("body") is not None or node.child_by_field_name("definition") is not None
    return has_body and node_name(node) is not None

def node_words(data:bytes,start:int,end:int)->int:
    return len(data[start:end].decode("utf-8","replace").split())

def visible(data:bytes,start:int,end:int)->int:
    return len(data[start:end].translate(None,b" \t\n\r\x0b\x0c"))#no space bytes

def children_hold_all(node:Node,data:bytes)->bool:#a string keeps its text between its children
    inside=sum(visible(data,child.start_byte,child.end_byte) for child in node.children)
    return node.child_count>0 and inside==visible(data,node.start_byte,node.end_byte)

def code_atoms(node:Node,data:bytes,words:int,path:list[str],atoms:list[tuple[Node,list[str]]])->None:
    for child in node.children:
        if node_words(data,child.start_byte,child.end_byte)<=words or not children_hold_all(child,data):
            atoms.append((child,path))
        else:#too big so we check inside for smaller parts
            name=node_name(child)
            inner=path+[name] if name and is_definition(child) else path
            code_atoms(child,data,words,inner,atoms)

def group_title(group:list[tuple[Node,list[str]]])->str:
    names=[]
    for node,path in group:
        name=node_name(node)
        if name and is_definition(node):
            names.append(" > ".join(path+[name]))
    if names:
        return ", ".join(names)
    deepest=max(group,key=lambda atom:len(atom[1]))#the first of the longest paths
    return " > ".join(deepest[1])

def char_at(data:bytes,byte:int)->int:
    return len(data[:byte].decode("utf-8","replace"))

def code_piece(text:str,data:bytes,group:list[tuple[Node,list[str]]])->tuple[int,int,str]:
    start,end=trimmed(text,char_at(data,group[0][0].start_byte),char_at(data,group[-1][0].end_byte))
    return (start,end,group_title(group))

def line_cut(group:list[tuple[Node,list[str]]],node:Node)->int|None:
    following=[atom[0] for atom in group[1:]]+[node]#we can cut before any of these
    for index in range(len(group),0,-1):#the latest place first
        if following[index-1].start_point[0]>group[index-1][0].end_point[0]:
            return index
    return None

def split_code(text:str,language:str,words:int)->list[tuple[int,int,str]]|None:
    if words<1:
        raise ValueError(f"In split_code words should be at least 1, current: {words}")
    parser=code_parser(language)
    if parser is None:
        return None
    data=text.encode("utf-8")
    tree=parser.parse(data)#the code tree
    atoms=[]
    code_atoms(tree.root_node,data,words,[],atoms)
    pieces=[]
    group=[]
    pending=atoms[::-1]#the last is the pending
    while pending:
        atom=pending.pop()
        node,path=atom
        if node_words(data,node.start_byte,node.end_byte)>words:
            if group:
                pieces.append(code_piece(text,data,group))
                group=[]
            start,end=char_at(data,node.start_byte),char_at(data,node.end_byte)
            for row_s,row_e in split_rows(text,start,end,words,words):
                pieces.append((row_s,row_e," > ".join(path)))
            continue
        while group and node_words(data,group[0][0].start_byte,node.end_byte)>words:
            cut=line_cut(group,node)
            if cut is None:
                break
            pieces.append(code_piece(text,data,group[:cut]))
            group=group[cut:]
        if group and node_words(data,group[0][0].start_byte,node.end_byte)>words and children_hold_all(node,data):
            name=node_name(node)
            inner=path+[name] if name and is_definition(node) else path
            for child in reversed(node.children):
                pending.append((child,inner))
            continue
        group.append(atom)
    if group:
        pieces.append(code_piece(text,data,group))
    return whole_lines(text,pieces)

def whole_lines(text:str,pieces:list[tuple[int,int,str]])->list[tuple[int,int,str]]:
    result=[]
    for start,end,title in pieces:
        while result and "\n" not in text[result[-1][1]:start]:#the last piece ends on the line where this one starts
            line_start,_=trimmed(text,text.rfind("\n",0,start)+1,start)#the whole line goes to this piece
            last_s,last_e,last_title=result.pop()
            last_s,last_e=trimmed(text,last_s,min(last_e,line_start))
            start=min(start,line_start)
            if last_s<last_e:
                result.append((last_s,last_e,last_title))
                break
        result.append((start,end,title))
    return result
