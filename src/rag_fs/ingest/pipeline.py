import multiprocessing
import os
import sqlite3
import tempfile
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor,as_completed
from dataclasses import dataclass,field,replace
from pathlib import Path
from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.archives import Budget,unpack
from rag_fs.ingest.cache import get_cached,put_cached
from rag_fs.ingest.file_scanner import ScannedFile, scan
from rag_fs.ingest.filetypes import MB,is_archive
from rag_fs.ingest.parsers import OCR_PARSERS,make_doc,parse_file
from rag_fs.ingest.parsers.ocr import check_ocr
from rag_fs.models import Document

START_METHOD="forkserver"

@dataclass
class ReadState:
    corpus:CorpusConfig
    ocr:OCRConfig=field(default_factory=OCRConfig)
    db:sqlite3.Connection|None=None#cache
    workers:int|None=None#count of processes
    skip:set[str]=field(default_factory=set)
    progress:Callable[[int,int],None]|None=None

def read_files(files:list[ScannedFile],corpus:CorpusConfig,ocr:OCRConfig=OCRConfig(),db:sqlite3.Connection|None=None,workers:int|None=None,progress:Callable[[int,int],None]|None=None)->list[Document]:
    check_ocr(ocr)
    state=ReadState(corpus,ocr,db,workers,progress=progress)
    for file in files:
        if not is_archive(file.rel_path):
            state.skip.add(file.sha256)#if we see this file again we will skip it
    parsed=parse_in_workers(files,state)
    docs=[]
    for file in files:
        if is_archive(file.rel_path):
            if file.sha256 in state.skip:
                continue
            state.skip.add(file.sha256)
        docs.extend(read_one(file,state,parsed))
    return docs

def read_archive(scanned:ScannedFile,state:ReadState,depth:int=1,budget:Budget|None=None)->list[Document]:
    corpus=state.corpus
    if depth>corpus.max_archive_depth:#too deep
        return [make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned)]
    if budget is None:
        budget=Budget(corpus.max_archive_size_mb*MB)
    docs=[]
    with tempfile.TemporaryDirectory() as tmp:#creates temp folder to unpack zip
        whole=unpack(scanned.abs_path,Path(tmp),corpus.max_file_size_mb*MB,budget)
        files=scan(replace(corpus,roots=[Path(tmp)])).files#we scan the temp as corpus
        new_files=[]
        for file in files:
            if file.sha256 not in state.skip:#files outside or in older archives are not read again
                new_files.append(file)
        parsed=parse_in_workers(new_files,state)
        for file in files:
            if file.sha256 in state.skip:#file already exists in corpus
                continue
            state.skip.add(file.sha256)
            for doc in read_one(file,state,parsed,depth+1,budget):
                docs.append(replace(doc,file_path=scanned.rel_path/doc.file_path,root_id=scanned.root_id))
    if not whole:
        docs.append(make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned))
    return docs

def read_one(file:ScannedFile,state:ReadState,parsed:dict[tuple[str,str],Document],depth:int=1,budget:Budget|None=None)->list[Document]:
    key=file_key(file)
    if key in parsed:
        return [replace(parsed[key],file_path=file.rel_path,root_id=file.root_id,last_modified=file.mtime)]#what workers read
    return read_file(file,state,depth,budget)#what to read

def read_file(file:ScannedFile,state:ReadState,depth:int=1,budget:Budget|None=None)->list[Document]:
    if not is_archive(file.rel_path):#normal file
        return [parse_cached(file,state)]
    return read_archive(file,state,depth,budget)

def parse_cached(file:ScannedFile,state:ReadState)->Document:
    if state.db is None:
        return parse_file(file,state.ocr)
    cached=get_cached(state.db,file)
    if cached is not None:#already parsed that file
        return cached
    doc=parse_file(file,state.ocr)
    put_cached(state.db,doc)
    return doc

def parse_in_workers(files:list[ScannedFile],state:ReadState)->dict[tuple[str,str],Document]:
    parsed={}
    if state.workers is None:
        return parsed
    todo={}
    for file in files:
        if is_archive(file.rel_path):
            continue
        key=file_key(file)
        if key in todo:#already marked for processing
            continue
        if state.db is not None and get_cached(state.db,file) is not None:#we have cache
            continue
        todo[key]=file
    order=sorted(todo.items(),key=lambda item: item[0][1] in OCR_PARSERS)#ocr goes last
    with ProcessPoolExecutor(max_workers=state.workers,mp_context=multiprocessing.get_context(START_METHOD),initializer=start_new_process) as pool:
        futures={}
        for key,file in order:
            future=pool.submit(parse_file,file,state.ocr)
            futures[future]=key
        for future in as_completed(futures):
            doc=future.result()
            if state.db is not None:#we cache them
                put_cached(state.db,doc)
            parsed[futures[future]]=doc
            if state.progress is not None:
                state.progress(len(parsed),len(order))
    return parsed

def start_new_process():
    os.nice(10)

def file_key(file:ScannedFile)->tuple[str,str]:
    return (file.sha256,file.rel_path.suffix.lower())