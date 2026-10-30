import os
import tempfile
from dataclasses import replace,dataclass
from pathlib import Path
import libarchive
from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.file_scanner import ScannedFile, scan,MB
from rag_fs.ingest.parsers import make_doc, parse_file,ARCHIVE_EXT
from rag_fs.models import Document

@dataclass
class Budget:#prevents zip bombs
    bytes_left:int

def unpack(path:Path,folder: Path,max_file_bytes:int,budget:Budget)->bool:
    raw_flag=False
    no_errors=True
    if path.suffix.lower()==".gz" and not path.name.lower().endswith(".tar.gz"):
        raw_flag=True
        reader=libarchive.file_reader(str(path),format_name="raw")
    else:
        reader=libarchive.file_reader(str(path))
    try:
        with reader as archive:
            for entry in archive:
                if not entry.isfile:
                    continue
                if raw_flag:
                    name=path.stem
                else:
                    name=entry.pathname
                if not name:
                    continue
                target=(folder/name).resolve()
                if folder.resolve() not in target.parents:#points outside
                    no_errors=False
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    f=open(target,"wb")
                except OSError:
                    no_errors=False
                    continue
                written=0
                try:
                    with f:
                        for block in entry.get_blocks():
                            written+=len(block)
                            budget.bytes_left-=len(block)
                            if budget.bytes_left<0 or written>max_file_bytes:
                                break
                            f.write(block)
                except libarchive.ArchiveError:#we couldnt write the file
                    target.unlink(missing_ok=True)#clean up
                    no_errors=False
                    continue
                if budget.bytes_left<0:#too big, possible bomb
                    target.unlink(missing_ok=True)
                    no_errors=False
                    return no_errors
                if written>max_file_bytes:#too large we ignore this file
                    target.unlink(missing_ok=True)
                    continue
                if entry.mtime:
                    os.utime(target,(entry.mtime,entry.mtime))#we give the zip file's time
    except libarchive.ArchiveError:
        no_errors=False
    return no_errors

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
            ext=file.rel_path.suffix.lower()
            if ext not in ARCHIVE_EXT:#normal file
                inner=[parse_file(file,ocr)]
            elif depth<corpus.max_archive_depth:
                inner=read_archive(file,config,skip,ocr,depth+1,budget)
            else:#too deep
                inner=[make_doc(ext,"","binary","failed",file)]
            for doc in inner:
                docs.append(replace(doc,file_path=scanned.rel_path/doc.file_path,root_id=scanned.root_id))
    if not whole:
        docs.append(make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned))
    return docs
