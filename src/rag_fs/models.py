from dataclasses import dataclass
from pathlib import Path
import json
import dataclasses

@dataclass
class Document:
    file_path: Path
    root_id: str
    ext: str
    raw_text: str
    content_type: str
    parse_status: str
    sha256: str #to know if a file was changed

@dataclass
class Chunk:
    chunk_id: str
    file_path: Path
    char_start:int
    char_end: int
    text:str
    index_text:str
    content_type: str
    chunk_strategy: str 

@dataclass
class GoldSpan:
    file_path: Path
    char_start:int
    char_end: int
    sha256: str = "" 

@dataclass
class Query:
    qid: str
    topic_id:str
    evidence_type: str
    split: str
    text: str
    gold: list[GoldSpan]
    author: str
    key_term: str | None = None
    key_term_loc: str | None = None
    
#we save our objects as json so the rag dont have to read the corpus everytime
def save_jsonl(items,path):
    with open(path, "w",encoding="utf-8") as f:
        for item in items:
            d=dataclasses.asdict(item)#we save as dictionary
            path_to_string(d)
            f.write(json.dumps(d,ensure_ascii=False)+"\n")#ascii is false so we can read them for testing

#json dont know path so this function helps with that
def path_to_string(d):
    for key,value in d.items():
        if isinstance(value,Path):
            d[key]=value.as_posix()
        elif isinstance(value,list):
            for item in value:
                if isinstance(item,dict):
                    path_to_string(item)

def load_jsonl(cls,path):
    items=[]
    with open(path,encoding="utf-8") as f:
        for line in f:
            d=json.loads(line)
            items.append(dict_to_object(cls,d))
    return items

#from the saved dictionary we have to load the objects
def dict_to_object(cls,d):
    d=dict(d)
    if "file_path" in d:
        d["file_path"]= Path(d["file_path"])
    if "gold" in d:
        spans=[]
        for g in d["gold"]:
            spans.append(dict_to_object(GoldSpan,g))
        d["gold"]=spans
    return cls(**d)

def to_relative(file_path, root):
    file_path = Path(file_path).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    return file_path.relative_to(root)