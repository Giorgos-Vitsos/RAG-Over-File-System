import tempfile
from dataclasses import replace
from pathlib import Path
from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.archives import Budget,unpack
from rag_fs.ingest.file_scanner import ScannedFile, scan
from rag_fs.ingest.filetypes import MB,is_archive
from rag_fs.ingest.parsers import make_doc, parse_file,OCR_PARSERS
from rag_fs.ingest.parsers.ocr import check_ocr
from rag_fs.models import Document
import sqlite3
from rag_fs.ingest.cache import get_cached,put_cached
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor,as_completed

START_METHOD="forkserver"

def read_corpus(corpus:CorpusConfig,ocr:OCRConfig=OCRConfig(),db:sqlite3.Connection|None=None,workers:int|None=None)->list[Document]:
    check_ocr(ocr)
    files=scan(corpus).files
    docs_dict={}
    skip=set()
    if workers is not None:
        docs_dict=parse_in_workers(files,ocr,db,workers)#we run parallel
    for file in files:#first normal files then archives
        if not is_archive(file.rel_path):
            skip.add(file.sha256)
    docs=[]
    for file in files:
        if is_archive(file.rel_path):
            if file.sha256 in skip:#the same item
                continue
            skip.add(file.sha256)
        processed=replace_processed(file,docs_dict)
        if processed is not None:#if found via parallel
            docs.append(processed)
        else:
            docs.extend(read_file(file,corpus,skip,ocr,db=db,workers=workers))
    return docs

def read_file(file:ScannedFile,corpus:CorpusConfig,skip:set[str],ocr:OCRConfig=OCRConfig(),depth:int=1,budget:Budget|None=None,db:sqlite3.Connection|None=None,workers:int|None=None)->list[Document]:
    ext=file.rel_path.suffix.lower()
    if not is_archive(file.rel_path):#normal file
        return [parse_cached(file,ocr,db)]
    if depth>corpus.max_archive_depth:#too deep
        return [make_doc(ext,"","binary","failed",file)]
    return read_archive(file,corpus,skip,ocr,depth,budget,db=db,workers=workers)

def read_archive(scanned:ScannedFile,corpus:CorpusConfig,skip:set[str],ocr:OCRConfig=OCRConfig(),depth:int=1,budget:Budget|None=None,db:sqlite3.Connection|None=None,workers:int|None=None)->list[Document]:
    if budget is None:
        budget=Budget(corpus.max_archive_size_mb*MB)
    docs=[]
    with tempfile.TemporaryDirectory() as tmp:#creates temp folder to unpack zip
        whole=unpack(scanned.abs_path,Path(tmp),corpus.max_file_size_mb*MB,budget)
        config=replace(corpus, roots=[Path(tmp)])
        result=scan(config)#we scan the temp as corpus
        docs_dict={}
        if workers is not None:
            new_files=[]
            for file in result.files:
                if file.sha256 not in skip:
                    new_files.append(file)
            docs_dict=parse_in_workers(new_files,ocr,db,workers)
        for file in result.files:
            if file.sha256 in skip:#file already exists in corpus
                continue
            skip.add(file.sha256)
            processed=replace_processed(file,docs_dict)
            if processed is not None:
                found=[processed]
            else:
                found=read_file(file,config,skip,ocr,depth+1,budget,db=db,workers=workers)
            for doc in found:
                docs.append(replace(doc,file_path=scanned.rel_path/doc.file_path,root_id=scanned.root_id))
    if not whole:
        docs.append(make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned))
    return docs

def parse_cached(file:ScannedFile,ocr:OCRConfig=OCRConfig(),db:sqlite3.Connection|None=None)->Document:
    if db is None:
        return parse_file(file,ocr)
    cached=get_cached(db,file)
    if cached is not None:#already parsed that file
        return cached
    doc=parse_file(file,ocr)
    put_cached(db,doc)
    return doc

def start_new_process():
    os.nice(10)

def parse_in_workers(files:list[ScannedFile],ocr:OCRConfig,db:sqlite3.Connection|None,workers:int)->dict[tuple[str,str],Document]:
    todo={}
    for file in  files:
        if is_archive(file.rel_path):
            continue
        key=(file.sha256,file.rel_path.suffix.lower())
        if  key in todo:#already marked for processing
            continue
        if db is not None and get_cached(db,file) is not None:#we have cache
            continue
        todo[key]=file
    order=sorted(todo.items(),key=lambda item: item[0][1] in OCR_PARSERS)#ocr goes last
    parsed={}
    with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context(START_METHOD),initializer=start_new_process) as pool:
        futures={}
        for key,file in order:
            future=pool.submit(parse_file,file,ocr)
            futures[future]=key
        for future in as_completed(futures):
            doc=future.result()
            if db is not None:#we cache them
                put_cached(db,doc)
            parsed[futures[future]]=doc
    return parsed

def replace_processed(file:ScannedFile,docs_dict:dict[tuple[str,str],Document])->Document|None:
    key=(file.sha256,file.rel_path.suffix.lower())
    if key in docs_dict:
        return replace(docs_dict[key],file_path=file.rel_path,root_id=file.root_id,last_modified=file.mtime)
    return None