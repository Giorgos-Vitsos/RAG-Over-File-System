from dataclasses import dataclass,field
from pathlib import Path
import hashlib
import os
from rag_fs.config import CorpusConfig
from rag_fs.models import to_relative
import fnmatch
from collections import Counter
import argparse

MB=1024*1024

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
        n=MB       
        block=f.read(n)
        while(block!=b""):#we read the binary in blocks so we dont need to load the whole file
            hash_temp.update(block)    
            block=f.read(n)
    return hash_temp.hexdigest()

def scan(corpus: CorpusConfig)->ScannerResult:
    result=ScannerResult()
    max_allowed_bytes=corpus.max_file_size_mb*(MB)
    for i,root in enumerate(corpus.roots):
        root_id=f"root_{i}"
        if not root.is_dir(): #we check if we can walk in this dir (allowed or able)
            raise FileNotFoundError(f"File scanner wasnt able to find {root}")
        for part in root.parts:
            if(matches(part,corpus.ignore_dirs)):
                raise ValueError(f"Corpus root: {root} is not allowed, part: {part} is in the ignore list. Check your settings")
        for dirpath,dirnames,filenames in os.walk(root,followlinks=corpus.follow_symlinks):
            keep=[]
            for d in dirnames: #for every dir insdie the current dir
                if matches(d,corpus.ignore_dirs):
                    result.ignored.append((Path(dirpath)/d,"ignored_dir"))
                else:
                    keep.append(d)
            dirnames[:]=sorted(keep)#we want next loop to be only inside the allowed dirs
            for name in sorted(filenames):
                path=Path(dirpath) / name
                if matches(name,corpus.ignore_globs):
                    result.ignored.append((path,"ignored_file"))
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
                    if info.st_size > max_allowed_bytes:
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


def count_by_extension(result: ScannerResult)->Counter:
    file_count=Counter()
    for file in result.files:
        file_type=file.rel_path.suffix.lower()
        if file_type=="":
            file_type="(none)"
        file_count[file_type]+=1
    return file_count

def count_ignored_by_reason(result: ScannerResult)->Counter:
    ignore_count=Counter()
    for file in result.ignored:
        ignore_count[file[1]]+=1
    return ignore_count

def main():
    parser=argparse.ArgumentParser(description="Scan folders and report what the file scanner finds")
    parser.add_argument("roots",nargs="+",type=Path)
    parser.add_argument("--max-mb",type=int,default=50)
    args=parser.parse_args()
    roots=[]
    for r in args.roots:
        roots.append(r.expanduser().resolve())
    config=CorpusConfig(roots=roots,max_file_size_mb=args.max_mb)
    result=scan(config)
    print(f"Scanned {len(roots)} roots")
    print()
    print_results(result)
    
    
def print_results(result: ScannerResult):
    total_bytes=0
    for f in result.files:
        total_bytes+=f.size
    print(f"Found {len(result.files)} files ({human_readable_bytes(total_bytes)})")
    file_count=count_by_extension(result)
    for file_type,count in file_count.most_common():
        print(f"{file_type} \t {count}")
    print()
    print(f"Ignored files {len(result.ignored)}")
    ignore_count=count_ignored_by_reason(result)
    for reason,count in ignore_count.most_common():
        print(f"{reason} \t {count}")


def human_readable_bytes(size: int)->str:
    num=size
    unit=["B","KB","MB","GB","TB"]
    loop_count=0
    while num>=1024 and loop_count<len(unit)-1:
        num/=1024
        loop_count+=1
    return f"{num:.1f} {unit[loop_count]}"

    

if __name__ == "__main__":
    main()