from dataclasses import dataclass,field
from pathlib import Path
import hashlib
import os
from rag_fs.config import CorpusConfig
from rag_fs.ingest.filetypes import MB,is_archive
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


def scan(corpus: CorpusConfig)->ScannerResult:
    result=ScannerResult()
    max_allowed_bytes=corpus.max_file_size_mb*(MB)
    max_allowed_archive_bytes=corpus.max_archive_size_mb*(MB)
    for i,root in enumerate(corpus.roots):
        root_id=f"root_{i}"
        if not root.is_dir(): #we check if we can walk in this dir (allowed or able)
            raise FileNotFoundError(f"File scanner wasnt able to find {root}")
        for part in root.parts:
            if(matches(part,corpus.ignore_dirs)):
                raise ValueError(f"Corpus root: {root} is not allowed, part: {part} is in the ignore list. Check your settings")
        for dirpath,dirnames,filenames in os.walk(root,followlinks=corpus.follow_symlinks):
            siblings=set(filenames)|set(dirnames)
            keep=[]
            for d in dirnames: #for every dir insdie the current dir
                if matches(d,corpus.ignore_dirs):
                    result.ignored.append((Path(dirpath)/d,"ignored_dir"))
                elif is_generated(Path(dirpath)/d,corpus.ignore_dirs_with):
                    result.ignored.append((Path(dirpath)/d,"generated_dir"))
                else:
                    keep.append(d)
            dirnames[:]=sorted(keep)#we want next loop to be only inside the allowed dirs
            for name in sorted(filenames):
                path=Path(dirpath) / name
                if matches(name,corpus.ignore_globs):
                    result.ignored.append((path,"ignored_file"))
                    continue
                if is_sidecar(name,siblings,corpus.ignore_sidecars):
                    result.ignored.append((path,"sidecar_file"))
                    continue
                if(path.is_symlink() and not corpus.follow_symlinks):
                    result.ignored.append((path,"ignored_symlink"))
                    continue
                try:
                    rel_path=to_relative(path,root)
                except ValueError:
                    result.ignored.append((path, "outside_root"))
                    continue
                try:
                    info = path.stat()
                    if is_archive(path):#archives are dirs so size can be bigger
                        max_allowed=max_allowed_archive_bytes
                    else:
                        max_allowed=max_allowed_bytes
                    if info.st_size > max_allowed:
                        result.ignored.append((path, "too_large"))
                        continue
                    file_hash = file_sha256(path)#if this fails then is unreadable
                except OSError:
                    result.ignored.append((path, "unreadable"))
                    continue
                result.files.append(ScannedFile(root_id,path,rel_path,info.st_size,info.st_mtime,file_hash))
    return result

def matches(name: str,patterns: list[str])->bool:
    for pattern in patterns:
        if fnmatch.fnmatch(name,pattern): return True
    return False

def is_generated(path: Path,rules: dict[str,list[str]])->bool:
    for dirname,markers in rules.items():
        if not matches(path.name,[dirname]):#if it doesnt match the dirname no reason to test
            continue
        for _,dirnames,filenames in os.walk(path):
            for name in dirnames+filenames:#for every dir/file in the path
                if matches(name,markers):#if we find a marker then its probably generated
                    return True
    return False

def is_sidecar(name:str,siblings: set[str],patterns: list[str])->bool:
    if not matches(name,patterns):
        return False
    main=os.path.splitext(name)[0]#we split from the name the file type and we only keep the name
    return name!=main and main in siblings

def to_relative(file_path, root):
    file_path = Path(file_path).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    return file_path.relative_to(root)

def file_sha256(path: str | Path)->str:
    hash_temp=hashlib.sha256()
    with open(path,"rb") as f:
        n=MB
        block=f.read(n)
        while(block!=b""):#we read the binary in blocks so we dont need to load the whole file
            hash_temp.update(block)
            block=f.read(n)
    return hash_temp.hexdigest()
