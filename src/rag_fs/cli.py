from pathlib import Path
from collections import Counter
import argparse
import sys
from rag_fs.config import ChunkingConfig,CorpusConfig,load_config
from rag_fs.ingest.cache import drop_cached,open_cache,load_manifest,save_manifest
from rag_fs.ingest.file_scanner import ScannerResult,scan
from rag_fs.ingest.pipeline import read_files
from rag_fs.models import Document,save_jsonl,load_jsonl,Chunk
from rag_fs.ingest.quality import quality_reasons
from rag_fs.ingest.chunker import chunk_doc,length_stats
import json
import random

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
    show_cmd=commands.add_parser("show",help="show the parsed text")
    show_cmd.add_argument("config",type=Path)
    show_cmd.add_argument("file",nargs="?",default=None)
    show_cmd.add_argument("--status",choices=STATUSES)
    show_cmd.add_argument("--quality",action="store_true",default=False)
    show_cmd.add_argument("--chunks",action="store_true",default=False)
    chunk_cmd=commands.add_parser("chunk", help="chunks the parsed data")
    chunk_cmd.add_argument("config",type=Path)
    chunk_cmd.add_argument("--sample",type=int,metavar="N",help="returns samples of the chunking")


    args=parser.parse_args(argv)
    if args.command=="scan":
        run_scan(args)
    elif args.command=="ingest":
        run_ingest(args,parser)
    elif args.command=="show":
        run_show(args,parser)
    elif args.command=="chunk":
        run_chunk(args,parser)

def run_show(args:argparse.Namespace,parser:argparse.ArgumentParser)->None:
    config=load_config(args.config)
    if sum([args.file is not None,args.status is not None,args.quality])!=1:
        parser.error("Wrong number of arguments give one of: a file, --status, --quality")
    if args.chunks and args.file is None:
        parser.error("--chunks needs a file")
    out=Path(config.ingest.documents)
    if not out.is_file():
        parser.error("No parsed text, run ingestion first")
    docs=load_jsonl(Document,out)
    if args.status is not None:
        paths=[]
        for doc in docs:
            if doc.parse_status==args.status:
                paths.append(str(doc.file_path))
        for path in sorted(paths):#all with the status asked
            print(path)
    elif args.quality:
        for doc in sorted(docs,key=lambda d: str(d.file_path)):
            reasons=quality_reasons(doc)
            if reasons:
                print(f"{doc.file_path}: {', '.join(reasons)}")
    else:
        found=[]
        for doc in docs:
            if str(doc.file_path)==args.file or doc.file_path.name==args.file:
                found.append(doc)
        if not found:
            parser.error(f"No {args.file} was found in the parsed files")
        for doc in found:
            if args.chunks:
                print_chunks(doc,config.chunking)
            else:
                print_doc(doc)

def print_doc(doc:Document)->None:
    text=doc.raw_text
    print(f"{doc.file_path}    ({doc.root_id})")
    print()
    print(f"status:{doc.parse_status}    type:{doc.content_type}    len:{len(text)}")
    reasons=quality_reasons(doc)
    if reasons:
        print(f"quality:{', '.join(reasons)}")
    print()
    print(text)

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
    result=scan(config.corpus,load_manifest(db))
    save_manifest(db,result.files)
    inner_ignored=[]#ignored inside archives
    docs=read_files(result.files,config.corpus,config.ocr,db,config.ingest.workers,show_progress,inner_ignored)
    print(file=sys.stderr)#ends the progress line
    out=Path(config.ingest.documents)
    out.parent.mkdir(parents=True,exist_ok=True)
    save_jsonl(docs,out)
    print_summary(docs,ScannerResult(result.files,result.ignored+inner_ignored))

def run_chunk(args:argparse.Namespace,parser:argparse.ArgumentParser)->None:
    config=load_config(args.config)
    if args.sample is not None:
        if args.sample<1:
            parser.error("Sample should be at least 1")
    documents=Path(config.ingest.documents)
    if not documents.is_file():
        parser.error("No parsed texts, run ingestion first")
    docs=load_jsonl(Document,documents)
    chunks=[]
    for doc in docs:#for each parsed file we chunk it
        chunks.extend(chunk_doc(doc,config.chunking))
    out=Path(config.chunking.chunks_dir)#the folder where we save the chunks
    out.mkdir(parents=True,exist_ok=True)
    name=f"chunks-{config.chunking.strategy}"#different name for different strategies
    save_jsonl(chunks,out/f"{name}.jsonl")
    by_type={}
    for chunk in chunks:
        kind=chunk.content_type
        if kind not in by_type:
            by_type[kind]=[]
        by_type[kind].append(chunk)
    stats={
        "strategy": config.chunking.strategy,
        "settings": {"narrative_words": config.chunking.narrative_words, "overlap": config.chunking.overlap, "tabular_rows": config.chunking.tabular_rows},
        "documents": len(docs),
        "all": length_stats(chunks),
        "by_type": {},
    }
    for kind in sorted(by_type):
        stats["by_type"][kind]=length_stats(by_type[kind])
    (out/f"{name}.stats.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Chunks {len(chunks)} from {len(docs)} documents ({config.chunking.strategy})")
    print()
    for kind in sorted(by_type):
        print(stats_line(kind,stats["by_type"][kind]))
    print(stats_line("all",stats["all"]))
    if args.sample is not None:
        print_sample(by_type,args.sample,config.seed)


def stats_line(name:str,stats:dict)->str:
    words=stats["words"]
    chars=stats["chars"]
    return (f"{name}: {stats['chunks']} chunks, {stats['empty']} empty, "
            f"words {words['mean']:.1f}/{words['median']:.1f}/{words['p90']}, chars {chars['mean']:.1f}/{chars['median']:.1f}/{chars['p90']}")
    
def print_sample(by_type:dict[str,list[Chunk]],sample:int,seed:int)->None:
    for kind in sorted(by_type):
        not_empty=[]
        for c in by_type[kind]:
            if c.text:#only chunks with text, the empty ones have nothing to check
                not_empty.append(c)
        if not not_empty:#none of this type
            continue
        winner=random.Random(seed).sample(not_empty,min(sample,len(not_empty)))
        print()
        print(f"=== {kind}: {len(winner)} of {len(not_empty)} ===")
        for c in winner:
            print_chunk(c)

def print_chunks(doc:Document,chunking:ChunkingConfig)->None:
    chunks=chunk_doc(doc,chunking)
    print(f"{doc.file_path}    ({doc.root_id})")
    print()
    print(f"chunks {len(chunks)} ({chunking.strategy})")
    print()
    for c in chunks:
        print_chunk(c)

def print_chunk(chunk:Chunk)->None:
    print(f"--- {chunk.chunk_id}")
    print(chunk.index_text)
    print()            

def ext_of(text:str)->str:
    text=text.lower()
    if not text.startswith("."):
        text="."+text
    return text

def show_progress(done:int,total:int)->None:
    print(f"\r\033[Kread {done}/{total} files",end="",file=sys.stderr,flush=True)

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