from pathlib import Path
from collections import Counter
import argparse
from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import ScannerResult,scan

def main():
    parser=argparse.ArgumentParser(description="Scan folders and report what the file scanner finds")
    parser.add_argument("roots",nargs="+",type=Path)
    parser.add_argument("--max-mb",type=int,default=CorpusConfig.max_file_size_mb)
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
