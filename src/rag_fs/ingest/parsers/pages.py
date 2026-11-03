import re
from collections import Counter
import unicodedata

LIGATURE=re.compile("[\ufb00-\ufb06]")#one character for two or three letters
BROKEN_WORD=re.compile(r"(\w+)-\n(\w+)")#a word cut by a hyphen at the end of a line
END_BROKEN=re.compile(r"(\w+)-$")#a word and a hyphen at the very end
START_WORD=re.compile(r"(\w+)")
HYPHENATED_WORD=re.compile(r"\w+(?:-\w+)*")#a word together with its hyphens
NUMBER=re.compile(r"\d+")

def edge_key(text: str)->str:
    return NUMBER.sub("#"," ".join(text.split()).lower())#different pages give the same key if the dig is the only difference

def count_edges(edges:list[list[str]],page_count:int)->tuple[set[str],dict[str,set[str]]]:#the keys found on half of the pages and how each key was written
    counter=Counter()
    variants={}#keeps all the variants of repeated text
    for texts in edges:
        keys=set()
        for text in texts:
            keys.add(edge_key(text))
            variants.setdefault(edge_key(text),set()).add(" ".join(text.split()).lower())
        counter.update(keys)
    repeated=set()
    if page_count>=3:#not enough pages to find headers
        for key,count in counter.items():
            if count>=page_count/2:
                repeated.add(key)
    return repeated,variants

def drop_repeated_edges(pages:list[list[str]])->tuple[list[list[str]],list[tuple[int,str]]]:
    if len(pages)<3:#not enough pages to find headers
        return pages,[]
    edges=[]
    for paragraphs in pages:
        texts=[]
        if len(paragraphs)>=2:
            for index,text in enumerate(paragraphs):
                if index<2 or index >=len(paragraphs)-2:
                    texts.append(text)
        edges.append(texts)
    repeated,variants=count_edges(edges,len(pages))
    output=[]
    titles=[]
    seen=set()
    for page_index,paragraphs in enumerate(pages):
        kept=[]
        for index,text in enumerate(paragraphs):
            if edge_key(text) in repeated and (index<2 or index >=len(paragraphs)-2):#we drop edges that repeat
                if len(variants[edge_key(text)])==1 and edge_key(text) not in seen:#if it has no variants then its probably a title
                    seen.add(edge_key(text))
                    titles.append((page_index,text))
                continue
            kept.append(text)
        output.append(kept)
    return output,titles

def block_texts(text:str,rows:list[list[str]]|None)->list[str]:#a block has either text or the cells of a table
    if rows is None:
        return [text]
    cells=[]
    for row in rows:
        for cell in row:
            cells.append(cell)
    return cells

def get_words(pages:  list[list[tuple[str,list[list[str]]|None]]])->set[str]:#it returns all words other than "-\n" connected
    words=set()
    for blocks in pages:
        for text,rows in blocks:
            for part in block_texts(text,rows):
                whole=BROKEN_WORD.sub(" ",part)
                for word in HYPHENATED_WORD.findall(whole):
                    words.add(word.lower())
    return words

def fix_text_block(block:str,words:set[str])->str:
    block=BROKEN_WORD.sub(lambda match: join_broken(match.group(1),match.group(2),words),block)#fixes broken words "-"
    block=LIGATURE.sub(lambda match: unicodedata.normalize("NFKC",match.group()),block)#fixes combined symbols
    return " ".join(block.split())

def join_broken(word1:str,word2:str,words:set[str])->str:
    joined=word1+word2
    hyphenated=word1+"-"+word2
    if not word1.isupper() or not word2.isupper():
        if word1.isupper() or not word2[0].islower():
            return hyphenated
    if joined.lower() not in words and hyphenated.lower() in words:#we check how its written elsewhere
        return hyphenated
    return joined

def join_broken_pages(page1:str,page2:str,words:set[str])->str|None:
    end=END_BROKEN.search(page1)
    start=START_WORD.match(page2)
    if end is None or start is None:
        return None
    if not start.group(1)[0].islower():
        return None
    return page1[:end.start()]+join_broken(end.group(1),start.group(1),words)+page2[start.end():]
