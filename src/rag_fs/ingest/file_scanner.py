from dataclasses import dataclass,field
from pathlib import Path
import hashlib
import os
from rag_fs.config import CorpusConfig
from rag_fs.models import to_relative
from rag_fs.models import path_to_string
import fnmatch

@dataclass(frozen=True)
class ScannedFile:
    root_id: str
    abs_path: Path
    rel_path: Path
    size: int
    mtime: float
    sha256: str

@dataclass
class ScannerResult:
    files: list[ScannedFile]=field(default_factory=list)
    ignored: list[tuple[Path,str]]=field(default_factory=list)


def file_sha256(path: str | Path)->str:
    hash_temp=hashlib.sha256()
    with open(path,"rb") as f:
        n=1024*1024        
        block=f.read(n)
        while(block!=b""):
            hash_temp.update(block)    
            block=f.read(n)
    return hash_temp.hexdigest()

def scan(corpus: CorpusConfig)->ScannerResult:
    result=ScannerResult()
    max_allowed_bytes=corpus.max_file_size_mb*(1024*1024)
    for i,root in enumerate(corpus.roots):
        root_id=f"root_{i}"
        if not root.is_dir():
            raise FileNotFoundError(f"File scanner wasnt able to find {root}")
        for part in root.parts:
            if(matches(part,corpus.ignore_dirs)):
                raise ValueError(f"Corpus root: {root} is not allowed, part: {part} is in the ignore list. Check your settings")
        for dirpath,dirnames,filenames in os.walk(root,followlinks=corpus.follow_symlinks):
            keep=[]
            for d in dirnames:
                if matches(d,corpus.ignore_dirs):
                    result.ignored.append((Path(dirpath)/d,"ignored_dir"))
                else:
                    keep.append(d)
            dirnames[:]=sorted(keep)
            for name in sorted(filenames):
                path=Path(dirpath) / name
                if matches(name,corpus.ignore_globs):
                    result.ignored.append((path,"ignored_file"))
                    continue
                if(path.is_symlink() and not corpus.follow_symlinks):
                    result.ignored.append((path,"ignored_symlink"))
                    continue

                rel_path=to_relative(path,root)
                try:
                    info = path.stat()
                    if info.st_size > max_allowed_bytes:
                        result.ignored.append((path, "too_large"))
                        continue
                    file_hash = file_sha256(path)
                except OSError:
                    result.ignored.append((path, "unreadable"))
                    continue
                result.files.append(ScannedFile(root_id,path,rel_path,info.st_size,info.st_mtime,file_hash))
    return result


def matches(name: str,patterns: list[str])->bool:
    for pattern in patterns:
        if fnmatch.fnmatch(name,pattern): return True
    return False