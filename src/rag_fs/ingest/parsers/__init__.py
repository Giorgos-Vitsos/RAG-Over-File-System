from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.models import Document
from .text import read_text
from .tabular import read_csv,read_ods,read_xlsx,read_xls
from .office import read_docx,read_word_variants,read_pptx
import logging

log = logging.getLogger(__name__)
CODE_EXT={".py",".js",".ts",".java",".c",".cpp",".h",".cs",".go",".dart",".rs",".sh",".sql",".r",".m",".php",".rb",".kt",".swift"}
TABULAR_EXT={".csv",".tsv",".xlsx",".xls",".ods"}
BINARY_EXT={".zip", ".gz", ".7z", ".rar", ".tar", ".exe", ".dll", ".so", ".bin", ".iso", ".mp3", ".wav", ".flac", ".mp4", ".mkv", 
            ".avi", ".mov", ".ttf",".woff", ".pyc", ".class", ".jar", ".o", ".db", ".sqlite"}

PARSERS={
    ".txt": read_text,
    ".md": read_text,
    ".markdown": read_text,
    ".tex": read_text,
    ".bib": read_text,
    ".srt": read_text,
    ".ini": read_text,
    ".cfg": read_text,
    ".yaml": read_text,
    ".yml": read_text,
    ".toml": read_text,
    ".csv": read_csv,
    ".xlsx": read_xlsx,
    ".ods": read_ods,
    ".tsv": read_csv,
    ".xls": read_xls,
    ".docx": read_docx,
    ".docm": read_word_variants,
    ".dotx": read_word_variants,
    ".dotm": read_word_variants,
    ".pptx": read_pptx
}

for ext in CODE_EXT:
    PARSERS[ext]=read_text
  
def parse_file(scanned: ScannedFile)->Document:
    ext=scanned.rel_path.suffix.lower()
    if ext in BINARY_EXT:
        return make_doc(ext,"","binary","unsupported",scanned)
    parser=PARSERS.get(ext,read_text)
    try:
        text=parser(scanned.abs_path)
    except Exception as e:
        log.warning(f"cannot parse {scanned.rel_path}: {e}")
        if ext in PARSERS:
            return make_doc(ext,"",content_type_of(ext),"failed",scanned)
        else:
            return make_doc(ext,"","binary","unsupported",scanned)
    text=text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("\ufeff")
    if text.strip()=="":
        return make_doc(ext,text,content_type_of(ext),"empty",scanned)
    return make_doc(ext,text,content_type_of(ext),"ok",scanned)


def make_doc(ext,text,content_type,status,scanned:ScannedFile,)->Document:
    return Document(scanned.rel_path,scanned.root_id,ext,text,content_type,status,scanned.sha256)

def content_type_of(ext):
    if ext in CODE_EXT:
        return "code"
    elif ext in TABULAR_EXT:
        return "tabular"
    else:
        return "prose"