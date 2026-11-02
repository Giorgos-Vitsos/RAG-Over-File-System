from pathlib import Path
from collections import Counter
import argparse
import sys
from rag_fs.config import CorpusConfig,load_config
from rag_fs.ingest.cache import drop_cached,open_cache
from rag_fs.ingest.file_scanner import ScannerResult,scan
from rag_fs.ingest.pipeline import read_files
from rag_fs.models import Document,save_jsonl

STATUSES=["ok","ocr","empty","failed","unsupported"]

def main(argv:list[str]|None=None):
    parser=argparse.ArgumentParser(description="RAG over a local file system")
    commands=parser.add_subparsers(dest="command",required=True)
    scan_cmd=commands.add_parser("scan",help="report what the file scanner finds")
    scan_cmd.add_argument("roots",nargs="+",type=Path)
    scan_cmd.add_argument("--max-mb",type=int,default=CorpusConfig.max_file_size_mb)
    ingest_cmd=commands.add_parser("ingest",help="read the corpus into documents")
    ingest_cmd.add_argument("config",type=Path)
    ingest_cmd.add_argument("--rebuild",nargs="*",metavar="EXT",help="read these extensions again, or everything without any")
    args=parser.parse_args(argv)
    if args.command=="scan":
        run_scan(args)
    elif args.command=="ingest":
        run_ingest(args,parser)

def run_scan(args:argparse.Namespace)->None:
    roots=[]
    for r in args.roots:
        roots.append(r.expanduser().resolve())
    config=CorpusConfig(roots=roots,max_file_size_mb=args.max_mb)
    result=scan(config)
    print(f"Scanned {len(roots)} roots")
    print()
    print_results(result)

def run_ingest(args:argparse.Namespace,parser:argparse.ArgumentParser)->None:
    config=load_config(args.config)
    if not config.corpus.roots:
        parser.error("The config has no corpus roots")
    db=open_cache(Path(config.ingest.cache))
    if args.rebuild is not None:#if we have rebuild command
        if args.rebuild:
            exts=[]
            for text in args.rebuild:
                exts.append(ext_of(text))
            drop_cached(db,exts)
        else:#without extensions we rebuild everything
            drop_cached(db,None)
    result=scan(config.corpus)
    docs=read_files(result.files,config.corpus,config.ocr,db,config.ingest.workers,show_progress)
    print(file=sys.stderr)#ends the progress line
    out=Path(config.ingest.documents)
    out.parent.mkdir(parents=True,exist_ok=True)
    save_jsonl(docs,out)
    print_summary(docs,result)

def ext_of(text:str)->str:
    text=text.lower()
    if not text.startswith("."):
        text="."+text
    return text

def show_progress(done:int,total:int)->None:
    print(f"\rread {done}/{total} files",end="",file=sys.stderr,flush=True)

def print_summary(docs:list[Document],result:ScannerResult)->None:
    statuses=Counter()
    by_ext={}
    for doc in docs:
        statuses[doc.parse_status]+=1
        ext=doc.ext or "(none)"
        if ext not in by_ext:
            by_ext[ext]=Counter()
        by_ext[ext][doc.parse_status]+=1
    print(f"Documents {len(docs)}")
    for status in STATUSES:
        if statuses[status]>0:
            print(f"{status} {statuses[status]}")
    print()
    for ext,counts in sorted(by_ext.items(),key=lambda item: -sum(item[1].values())):#most documents first
        parts=[]
        for status in STATUSES:
            if counts[status]>0:
                parts.append(f"{status} {counts[status]}")
        print(ext+" "+", ".join(parts))
    print()
    print(f"Ignored {len(result.ignored)}")
    for reason,count in count_ignored_by_reason(result).most_common():
        print(f"{reason} {count}")

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