import os
import tempfile
from dataclasses import replace
from pathlib import Path
import libarchive
from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.file_scanner import ScannedFile, scan
from rag_fs.ingest.parsers import make_doc, parse_file
from rag_fs.models import Document

def unpack(path:Path,folder: Path)->bool:
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
                target=folder/name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(target,"wb") as f:
                        for block in entry.get_blocks():
                            f.write(block)
                except libarchive.ArchiveError:#we couldnt write the file
                    target.unlink(missing_ok=True)#clean up
                    no_errors=False
                    continue
                if entry.mtime:
                    os.utime(target,(entry.mtime,entry.mtime))#we give the zip file's time
    except libarchive.ArchiveError:
        no_errors=False
    return no_errors

def read_archive(scanned:ScannedFile,corpus:CorpusConfig,skip:set[str],ocr:OCRConfig=OCRConfig())->list[Document]:
    docs=[]
    with tempfile.TemporaryDirectory() as tmp:#creates temp folder to unpack zip
        whole=unpack(scanned.abs_path,Path(tmp))
        config=replace(corpus, roots=[Path(tmp)])
        result=scan(config)#we scan the temp as corpus
        for file in result.files:
            if file.sha256 in skip:#file already exists in corpus
                continue
            skip.add(file.sha256)
            doc=parse_file(file,ocr)
            docs.append(replace(doc,file_path=scanned.rel_path/file.rel_path,root_id=scanned.root_id))
    if not whole:
        docs.append(make_doc(scanned.rel_path.suffix.lower(),"","binary","failed",scanned))
    return docs
