import tempfile
from dataclasses import replace
from pathlib import Path
from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.archives import Budget,unpack
from rag_fs.ingest.file_scanner import ScannedFile, scan
from rag_fs.ingest.filetypes import MB,is_archive
from rag_fs.ingest.parsers import make_doc, parse_file
from rag_fs.ingest.parsers.ocr import check_ocr
from rag_fs.models import Document

def read_corpus(corpus:CorpusConfig,ocr:OCRConfig=OCRConfig())->list[Document]:
    check_ocr(ocr)
    files=scan(corpus).files
    skip=set()
    for file in files:#first normal files then archives
        if not is_archive(file.rel_path):
            skip.add(file.sha256)
    docs=[]
    for file in files:
        if is_archive(file.rel_path):
            if file.sha256 in skip:#the same item
                continue
            skip.add(file.sha256)
        docs.extend(read_file(file,corpus,skip,ocr))
    return docs

def read_file(file:ScannedFile,corpus:CorpusConfig,skip:set[str],ocr:OCRConfig=OCRConfig(),depth:int=1,budget:Budget|None=None)->list[Document]:
    ext=file.rel_path.suffix.lower()
    if not is_archive(file.rel_path):#normal file
        return [parse_file(file,ocr)]
    if depth>corpus.max_archive_depth:#too deep
        return [make_doc(ext,"","binary","failed",file)]
    return read_archive(file,corpus,skip,ocr,depth,budget)

def read_archive(scanned:ScannedFile,corpus:CorpusConfig,skip:set[str],ocr:OCRConfig=OCRConfig(),depth:int=1,budget:Budget|None=None)->list[Document]:
    if budget is None:
        budget=Budget(corpus.max_archive_size_mb*MB)
    docs=[]
    with tempfile.TemporaryDirectory() as tmp:#creates temp folder to unpack zip
        whole=unpack(scanned.abs_path,Path(tmp),corpus.max_file_size_mb*MB,budget)
        config=replace(corpus, roots=[Path(tmp)])
        result=scan(config)#we scan the temp as corpus
        for file in result.files:
            if file.sha256 in skip:#file already exists in corpus
                continue
            skip.add(file.sha256)
            for doc in read_file(file,config,skip,ocr,depth+1,budget):
                docs.append(replace(doc,file_path=scanned.rel_path/doc.file_path,root_id=scanned.root_id))
    if not whole:
        docs.append(make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned))
    return docs
