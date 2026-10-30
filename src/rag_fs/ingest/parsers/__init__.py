import logging
from rag_fs.config import OCRConfig
from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.ingest.filetypes import BINARY_EXT,CODE_EXT,TABULAR_EXT
from rag_fs.models import Document
from .markdown import read_markdown
from .ocr import read_image
from .office import read_docx,read_pptx,read_slide_variants,read_word_variants
from .opendocument import read_odf,read_ods
from .pdf import read_pdf
from .rtf import read_rtf
from .tabular import read_csv,read_xls,read_xlsx
from .text import normalize,read_text,unify_newlines

log = logging.getLogger(__name__)

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
for ext in CODE_EXT:
    PARSERS[ext]=read_text

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
    text=unify_newlines(text).removeprefix("\ufeff")
    if content_type_of(ext)!="code":#in code we dont change any char
        text=normalize(text)
    if text.strip()=="":
        return make_doc(ext,text,content_type_of(ext),"empty",scanned)
    if used:
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
