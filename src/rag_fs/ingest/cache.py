import sqlite3
import zlib
from pathlib import Path
from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.models import Document
from rag_fs.ingest.parsers import make_doc

GOOD_STATUS={"ocr","ok","empty"}#empty means the file parsed correctly but its empty

def open_cache(path:Path)->sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS texts (sha256 TEXT, ext TEXT, text BLOB, content_type TEXT, parse_status TEXT, PRIMARY KEY (sha256, ext))")
    db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha256 TEXT)")
    return db

def put_cached(db:sqlite3.Connection,doc:Document)->None:
    if doc.parse_status not in GOOD_STATUS:
        return
    data=zlib.compress(doc.raw_text.encode("utf-8"))
    with db:
        db.execute("INSERT OR REPLACE INTO texts VALUES (?, ?, ?, ?, ?)", (doc.sha256,doc.ext,data,doc.content_type,doc.parse_status))

def get_cached(db:sqlite3.Connection,scanned:ScannedFile)->Document|None:
    ext=scanned.rel_path.suffix.lower()
    row=db.execute("SELECT text,content_type,parse_status FROM texts WHERE sha256=? AND ext=?", (scanned.sha256,ext)).fetchone()
    if row is None:
        return None
    text_b,content_type,parse_status=row
    text=zlib.decompress(text_b).decode("utf-8")
    return make_doc(ext,text,content_type,parse_status,scanned)

def drop_cached(db:sqlite3.Connection,exts: list[str]|None=None)->None:
    with db:
        if exts is None:
            db.execute("DELETE FROM texts")
        else:
            for ext in exts:
                db.execute("DELETE FROM texts WHERE ext=?",(ext,))

def save_manifest(db:sqlite3.Connection,files:list[ScannedFile])->None:
    rows=[]
    for file in files:
        rows.append((str(file.abs_path),file.size,file.mtime,file.sha256))
    with db:
        db.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)",rows)

def load_manifest(db:sqlite3.Connection)->dict[Path,tuple[int,float,str]]:
    known={}
    for path,size,time,sha256 in db.execute("SELECT path, size, mtime, sha256 FROM files"):
        known[Path(path)]=(size,time,sha256)
    return known