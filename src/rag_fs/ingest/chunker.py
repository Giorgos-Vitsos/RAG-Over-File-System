import re
from rag_fs.models import Document,Chunk
from rag_fs.config import ChunkingConfig
from pathlib import Path

HEADING=re.compile(r"(#{1,6}) (.*)")
FENCE="```"#code block
WORD=re.compile(r"\S+")

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
    chunks=[]
    for start,end,title in split_sections(doc.raw_text):
        for window_s,window_e in split_windows(doc.raw_text,start,end,config.narrative_words,config.overlap):
            chunks.append(make_chunk(doc,window_s,window_e,title,config.strategy))
    return chunks