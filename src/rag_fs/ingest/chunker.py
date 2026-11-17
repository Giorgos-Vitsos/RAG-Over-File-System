import re

HEADING=re.compile(r"(#{1,6}) (.*)")
FENCE="```"#code block

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
