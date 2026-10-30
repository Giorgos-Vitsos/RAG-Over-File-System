import os
from dataclasses import dataclass
from pathlib import Path
import libarchive

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
