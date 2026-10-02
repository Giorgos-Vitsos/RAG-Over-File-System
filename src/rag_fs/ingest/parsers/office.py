from pathlib import Path
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from .tabular import format_rows

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


def read_docx(path: Path)->str:
    doc=docx.Document(str(path))
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
