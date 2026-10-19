import re
import unicodedata
from pathlib import Path
import pymupdf
from collections import Counter

LIGATURE=re.compile("[\ufb00-\ufb06]")#one character for two or three letters
BROKEN_WORD=re.compile(r"(\w+)-\n(\w+)")#a word cut by a hyphen at the end of a line
WORD=re.compile(r"\w+(?:-\w+)*")#a word together with its hyphens
NUMBER=re.compile(r"\d+")
EDGE=0.12#the top and bottom part of a page

def read_pdf(path:Path)->str:
    with pymupdf.open(path) as file:
        if file.needs_pass:#if its password protected
            raise ValueError(f"The pdf {path} is password protected and cant be parsed")
        pages=[]
        for page in file:
            blocks=[]
            for block in page.get_text("blocks"):
                if block[6]==0:#block[6] is the type and 0 is text
                    blocks.append((block[1],block[3],block[4]))#block[1] is top,block[3] is bottom and block[4] is the text
            pages.append(blocks)
        slides=is_slides(file)
    pages=drop_headers(pages)#we remove the noise data
    words=get_words(pages)
    output=[]
    for i,blocks in enumerate(pages):
        if slides:
            output.append(f"# Slide {i+1}")
        for block in blocks:
            text=pdf_text_block(block,words)
            if text:
                output.append(text)
    return "\n\n".join(output)


def is_slides(file)->bool:
    landscape_counter=0
    for page in file:
        if page.rect.width>page.rect.height:
            landscape_counter+=1
    if landscape_counter>len(file)/2:#if most pages are landscape then its a slide
        return True
    return False

def get_words(pages: list[list[str]])->set[str]:#it returns all words other than "-\n" connected
    words=set()
    for blocks in pages:
        for block in blocks:
            whole=BROKEN_WORD.sub(" ",block)
            for word in WORD.findall(whole):
                words.add(word.lower())
    return words

def pdf_text_block(block:str,words:set[str])->str:
    block=BROKEN_WORD.sub(lambda match: join_broken(match.group(1),match.group(2),words),block)#fixes broken words "-"
    block=LIGATURE.sub(lambda match: unicodedata.normalize("NFKC",match.group()),block)#fixes combined symbols
    return " ".join(block.split())

def join_broken(word1:str,word2:str,words:set[str])->str:
    joined=word1+word2
    hyphenated=word1+"-"+word2
    if not word2[0].islower():#if the second part starts with capital then definitly not a broken word
        return hyphenated
    if hyphenated.lower() in words and joined.lower() not in words:
        return hyphenated
    return joined

def drop_headers(pages:list[tuple[float,list[tuple[float,float,str]]]])->list[list[str]]:
    counter=Counter()
    for height,blocks in pages:
        keys=set()
        for top,bottom,text in blocks:
            if on_edge(top,bottom,height):
                keys.add(edge_key(text))
        counter.update(keys)
    repeated=set()
    if len(pages)>=3:#pdfs with less than 3 pages are not good candidates
        for key,count in counter.items():
            if count>=len(pages)/2:
                repeated.add(key)
    output=[]
    for height,blocks in pages:#we pass again and remove the repeating ones
        texts=[]
        for top,bottom,text in blocks:
            if not on_edge(top,bottom,height) or not edge_key(text) in repeated:
                texts.append(text)
        output.append(texts)
    return output

def on_edge(top: float,bottom: float,height: float)->bool:
    return bottom<EDGE*height or top>(1-EDGE)*height

def edge_key(text: str)->str:
    return NUMBER.sub("#"," ".join(text.split()).lower())#different pages give the same key if the dig is the only difference