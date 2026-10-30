from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.models import Document
from .text import read_text
from .tabular import read_csv,read_xlsx,read_xls
from .office import read_docx,read_word_variants,read_pptx,read_slide_variants,read_rtf
from .ocr import read_image,fix_lookalikes
from .opendocument import read_ods,read_odf
import logging
from .markdown import read_markdown
from .pdf import read_pdf
import unicodedata
import re
from rag_fs.config import OCRConfig

log = logging.getLogger(__name__)
CODE_EXT={".py",".js",".ts",".java",".c",".cpp",".h",".cs",".go",".dart",".rs",".sh",".sql",".r",".m",".php",".rb",".kt",".swift",
          ".hpp",".hh",".hxx",".cc",".cxx",".inl",".hlsl",".glsl",".shader",".cginc",".compute",".frag",".vert",
          ".y",".l",".asm",".s",".cmake",".bat",".cmd",".ps1",".lua",".pl",".scala",".kts",".mm",".asc",".alpha"}
TABULAR_EXT={".csv",".tsv",".xlsx",".xls",".ods",".fods"}
BINARY_EXT={".zip", ".gz", ".7z", ".rar", ".tar", ".exe", ".dll", ".so", ".bin", ".iso", ".mp3", ".wav", ".flac", ".mp4", ".mkv", 
            ".avi", ".mov", ".ttf",".woff", ".pyc", ".class", ".jar", ".o", ".db", ".sqlite"}
ACCENT_START=re.compile(r"[\u0384\u00b4][ΑΕΗΙΟΥΩ]")#greek characters with accents that break
CP1252_SIGNS="".join(bytes([b]).decode("cp1252") for b in range(0x80,0xa0) if b not in (0x81,0x8d,0x8f,0x90,0x9d))
MOJIBAKE=re.compile("[\u0080-\u00ff"+re.escape(CP1252_SIGNS)+"]+")#possible broken characters

PARSERS={
    ".txt": read_text,
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
    ".fods": read_ods,
    ".tsv": read_csv,
    ".xls": read_xls,
    ".docx": read_docx,
    ".docm": read_word_variants,
    ".dotx": read_word_variants,
    ".dotm": read_word_variants,
    ".pptx": read_pptx,
    ".pptm": read_pptx,
    ".potx": read_slide_variants,
    ".potm": read_slide_variants,
    ".ppsx": read_slide_variants,
    ".ppsm": read_slide_variants,
    ".rtf": read_rtf,
    ".odt": read_odf,
    ".fodt": read_odf,
    ".ott": read_odf,
    ".odp": read_odf,
    ".fodp": read_odf,
    ".otp": read_odf,
    ".md": read_markdown,
    ".markdown": read_markdown,
}

OCR_PARSERS={
    ".png": read_image,
    ".jpg": read_image,
    ".jpeg": read_image,
    ".tif": read_image,
    ".tiff": read_image,
    ".bmp": read_image,
    ".gif": read_image,
    ".webp": read_image,
    ".pdf": read_pdf,
    ".xps": read_pdf,
    ".oxps": read_pdf,
}

for ext in CODE_EXT:
    PARSERS[ext]=read_text
  
def parse_file(scanned: ScannedFile,ocr:OCRConfig=OCRConfig())->Document:
    used=False
    ext=scanned.rel_path.suffix.lower()
    if ext in BINARY_EXT:
        return make_doc(ext,"","binary","unsupported",scanned)
    parser=PARSERS.get(ext,read_text)
    try:
        if ext in OCR_PARSERS:
            text,used=OCR_PARSERS[ext](scanned.abs_path,ocr)
        else:
            text=parser(scanned.abs_path)
    except Exception as e:
        log.warning(f"cannot parse {scanned.rel_path}: {e}")
        if ext in PARSERS or ext in OCR_PARSERS:
            return make_doc(ext,"",content_type_of(ext),"failed",scanned)
        else:
            return make_doc(ext,"","binary","unsupported",scanned)
    text=text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("\ufeff")
    if content_type_of(ext)!="code":#in code we dont change any char
        text=normalize(text)
    if text.strip()=="":
        return make_doc(ext,text,content_type_of(ext),"empty",scanned)
    if used:
        text=fix_lookalikes(text)
        return make_doc(ext,text,content_type_of(ext),"ocr",scanned)
    else:
        return make_doc(ext,text,content_type_of(ext),"ok",scanned)


def make_doc(ext,text,content_type,status,scanned:ScannedFile,)->Document:
    return Document(file_path=scanned.rel_path,root_id=scanned.root_id,ext=ext,raw_text=text,last_modified=scanned.mtime,content_type=content_type,parse_status=status,sha256=scanned.sha256)


def content_type_of(ext):
    if ext in CODE_EXT:
        return "code"
    elif ext in TABULAR_EXT:
        return "tabular"
    else:
        return "prose"

def normalize(text:str)->str:
    text=fix_mojibake(text)
    text=ACCENT_START.sub(lambda match: unicodedata.normalize("NFC",match.group(0)[-1]+"\u0301"),text)
    text=unicodedata.normalize("NFKC",text)
    return text

def fix_mojibake(text:str)->str:
    return MOJIBAKE.sub(lambda match: fix_mojibake_run(match.group(0)),text)

def fix_mojibake_run(text:str)->str:
    data=[]
    for c in text:
        if ord(c)<=0xff:
            data.append(ord(c))
        else:
            data.append(c.encode("cp1252")[0])
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:#not a mojibake
        return text