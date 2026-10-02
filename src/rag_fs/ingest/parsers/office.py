from pathlib import Path
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from .tabular import format_rows
from docx.document import Document as DocxDocument
import io
import zipfile


DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
WORD_VARIANTS=[
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml"
]

def heading_level(style_name:str)->int:
    if style_name=="Title":
        return 1
    elif style_name.startswith("Heading"):
        try:
            return int(style_name.split()[-1])
        except ValueError:
            return 0
    else:
        return 0


def docx_to_text(doc: DocxDocument)->str:
    blocks=[]
    for block in doc.iter_inner_content():
        if isinstance(block,Paragraph):
            for alt in block._p.xpath(".//wp:docPr/@descr"):
                if alt.strip():
                    blocks.append(f"[Image: {alt}]")

            text=block.text.strip()
            if not text:
                continue
            if not block.style is None:
                level=heading_level(str(block.style.name))
            else:
                level=0
            if level>0:
                text="#"*level+" "+text
            blocks.append(text)
        elif isinstance(block,Table):
            rows=[]
            for row in block.rows:
                cells=[]
                for cell in row.cells:
                    cells.append(cell.text)
                rows.append(cells)
            text=format_rows(rows)
            if text:
                blocks.append(text)
    return "\n\n".join(blocks)


def read_docx(path: Path)->str:
    doc=docx.Document(str(path))
    return docx_to_text(doc)

def read_word_variants(path: Path)->str:
    buffer=io.BytesIO()
    with zipfile.ZipFile(path) as src,zipfile.ZipFile(buffer, "w") as dst:
        for item in src.infolist():
            data=src.read(item.filename)
            if item.filename=="[Content_Types].xml":
                for variant in WORD_VARIANTS:
                    data=data.replace(variant.encode(),DOCX_MAIN.encode())
            dst.writestr(item,data)
    doc=docx.Document(buffer)
    return docx_to_text(doc)
        