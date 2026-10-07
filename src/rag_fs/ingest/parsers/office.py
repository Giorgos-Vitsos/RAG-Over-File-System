from pathlib import Path
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from .tabular import format_rows
from docx.document import Document as DocxDocument
import io
import zipfile
import pptx
from pptx.shapes.autoshape import Shape
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.presentation import Presentation as PptxPresentation


DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"#type of normal docx file
WORD_VARIANTS=[#types of doc variants
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml"
]

PPTX_MAIN="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"#type of normal pptx file
SLIDE_VARIANTS=[#types of ppt variants
    "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
    "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
    "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml"
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
            for alt in block._p.xpath(".//wp:docPr/@descr"):#we search for images
                if alt.strip():
                    blocks.append(f"[Image: {alt}]")
            text=block.text.strip()
            if not text:#if the block is empty
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

#variants of some files can still be parsed as the standard file, for example in docm we dont need the macros so we parse it as docx
def variants_to_main(path: Path,main: str,variants: list[str])->io.BytesIO:
    buffer=io.BytesIO()
    with zipfile.ZipFile(path) as src,zipfile.ZipFile(buffer, "w") as dst:#we open the zip file and a virtual one
        for item in src.infolist():
            data=src.read(item.filename)
            if item.filename=="[Content_Types].xml":#if the file is the one which holds the type of the variant
                for variant in variants:
                    data=data.replace(variant.encode(),main.encode())#we change it to the standard of that file type
            dst.writestr(item,data)#we copy everything to the virtual file
    return buffer

def read_word_variants(path: Path)->str:
    doc=docx.Document(variants_to_main(path,DOCX_MAIN,WORD_VARIANTS))
    return docx_to_text(doc)

def read_slide_variants(path: Path)->str:
    ppt=pptx.Presentation(variants_to_main(path,PPTX_MAIN,SLIDE_VARIANTS))
    return pptx_to_text(ppt)

def pptx_to_text(ppt: PptxPresentation)->str:
    output=[]
    for i,slide in enumerate(ppt.slides):
        block=[]
        title=slide.shapes.title
        for shape in slide.shapes:
            if title is not None and shape.shape_id==title.shape_id:#if we have a title we dont want to write it twice
                continue
            block.extend(format_shape(shape))
        header=f"# Slide {i+1}"
        if title is not None:#we build the header with the title if it exists
            title_txt=title.text_frame.text.strip()
            if title_txt:
                header=header+f": {title_txt}"  
        output.append(header)
        output.extend(block)
        if slide.has_notes_slide:#we add notes if they exist
            notes=slide.notes_slide.notes_text_frame
            if notes is not None:
                notes_txt=notes.text.strip()
                if notes_txt:
                    output.append(f"Notes: {notes_txt}")     
    return "\n\n".join(output)

def read_pptx(path: Path)->str:
    ppt=pptx.Presentation(str(path))
    return pptx_to_text(ppt)

def format_shape(shape:BaseShape)->list[str]:
    output=[]
    if isinstance(shape, GroupShape):
        for sub_shape in shape.shapes:
            output.extend(format_shape(sub_shape))
    elif isinstance(shape, Shape):
        text=shape.text_frame.text.strip()
        if text:
            output.append(text)
    elif isinstance(shape,GraphicFrame):
        if shape.has_table:
            rows=[]
            for row in shape.table.rows:
                cells=[]
                for cell in row.cells:
                    cells.append(cell.text)
                rows.append(cells)
            text=format_rows(rows).strip()
            if text:
                output.append(text)     
    for alt in shape._element.xpath("./*/p:cNvPr/@descr"):
        if alt.strip():
            output.append(f"[Image: {alt}]")
    return output