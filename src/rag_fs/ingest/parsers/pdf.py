from pathlib import Path
import pymupdf
from collections import Counter
from .tabular import format_rows
from .ocr import edge_key,drop_repeated_edges,get_words,fix_text_block,ocr_sample,run_ocr,WORD,join_broken_pages
from PIL import Image
from rag_fs.config import OCRConfig
import unicodedata

EDGE=0.12#the top and bottom part of a page
TABLE_START=0.15#if a table starts here it might be cut
TABLE_END=0.85#if a table ends here it might be cut
CHAR_COUNT_OCR_FLAG=50
SUSPISIOUS_WORD_RATIO=0.2
CONTROL_CHAR_RATIO_FILTER=0.2#if we have more than that then the text is probably proken

def read_pdf(path:Path,ocr: OCRConfig=OCRConfig())->tuple[str,bool]:
    ocr_flag=False
    scanned=[]#OCR parsed page indexes
    with pymupdf.open(path) as file:
        if file.needs_pass:#if its password protected
            raise ValueError(f"The pdf {path} is password protected and cant be parsed")
        pages=[]
        last=None
        for page in file:
            tables,last=page_tables(page,last)
            blocks=[]
            placed=set()
            for block in page.get_text("blocks"):
                if block[6]!=0:#block[6] is the type and 0 is text
                    continue
                index=table_at(block,tables)
                if index is None:#not a table
                    blocks.append((block[1],block[3],block[4],None))
                elif index not in placed:
                    placed.add(index)
                    blocks.append((block[1],block[3],"",tables[index][1]))
            for index,(box,rows) in enumerate(tables):
                if index not in placed:
                    blocks.append((box[1],box[3],"",rows))
            if is_suspisious(blocks):
                fix_suspisious_blocks(blocks)
            pages.append((page.rect.height, blocks))
        pages,titles=drop_headers(pages)#we remove the noise data
        candidates=[]#candidates for ocr
        for index,page in enumerate(file.pages()):#we check for ocr inside the pdf page
            blocks=pages[index]
            char_count=0
            whitespace_count=0
            control_char_count=0
            for text,rows in blocks:
                if rows is None:#blocks have either text or rows never both
                    char_count+=len(text.strip())
                    for c in text.strip():
                        if c.isspace():
                            whitespace_count+=1
                        elif unicodedata.category(c)=="Cc":
                            control_char_count+=1
                else:
                    for row in rows:
                        for cell in row:
                            char_count+=len(cell.strip())
                            for c in cell.strip():
                                if c.isspace():
                                    whitespace_count+=1
                                elif unicodedata.category(c)=="Cc":
                                    control_char_count+=1
            if not ((char_count<CHAR_COUNT_OCR_FLAG and page.get_images()) or (control_char_count>(char_count-whitespace_count)*CONTROL_CHAR_RATIO_FILTER)):#no ocr needed
                continue
            candidates.append(index)
        samples,rest=ocr_sample(candidates)
        for group in (samples,rest):
            if group is rest and not scanned:#if no ocr images passed
                break
            for index in group:
                page=file.load_page(index)
                pix=page.get_pixmap(dpi=ocr.dpi)
                image=Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
                text_ocr=run_ocr(image,ocr)
                if text_ocr is None:
                    continue
                ocr_blocks=text_ocr.split("\n\n")
                new_blocks=[]
                for block in ocr_blocks:
                    new_blocks.append((block,None))
                pages[index]=new_blocks#we swap the block for the ocr one
                scanned.append(index)
                ocr_flag=True
        scanned.sort()
        slides=is_slides(file)
        bookmarks=file.get_toc()
    texts=[]
    for index in scanned:
        page_texts=[]
        for text,rows in pages[index]:
            page_texts.append(text)
        texts.append(page_texts)
    texts,ocr_titles = drop_repeated_edges(texts)#we remove the headers from all the ocr scanned
    for index,paragraphs in zip(scanned,texts):#we stich them again
        temp_paragraphs=[]
        for paragraph in paragraphs:
            temp_paragraphs.append((paragraph,None))
        pages[index]=temp_paragraphs
    for index,text in ocr_titles:
        titles.append((scanned[index],text))
    kept_titles=[]
    restored=set()
    for page_index,text in sorted(titles,key=lambda title:title[0]):#we sort so that we correctly locate the first appearance
        if edge_key(text) in restored:#already found             
            continue
        restored.add(edge_key(text))
        kept_titles.append((page_index,text))
    for page_index,text in reversed(kept_titles):
        pages[page_index].insert(0,(text,None)) 
    titles=add_headings(pages,bookmarks,slides)
    words=get_words(pages)
    output=[]
    last_was_text=False
    for i,blocks in enumerate(pages):
        if slides:
            header=f"# Slide {i+1}"
            if i in titles:
                header+=": "+titles[i]
            output.append(header)
            last_was_text=False
        for text,rows in blocks:
            if rows is None:
                text=fix_text_block(text,words)
                if last_was_text:#we search for broken pages
                    joined=join_broken_pages(output[-1],text,words)
                    if joined is not None:#we connect them
                        output[-1]=joined
                        continue
            else:
                clean=[]
                for row in rows:
                    clean_row=[]
                    for cell in row:
                        clean_row.append(fix_text_block(cell,words))
                    clean.append(clean_row)
                text=format_rows(clean)
            if text:
                output.append(text)
                if rows is None:#we appended text
                    last_was_text=True
                else:#we appanded table
                    last_was_text=False
    return "\n\n".join(output),ocr_flag

def is_slides(file)->bool:
    landscape_counter=0
    for page in file:
        if page.rect.width>page.rect.height:
            landscape_counter+=1
    if landscape_counter>len(file)/2:#if most pages are landscape then its a slide
        return True
    return False

def drop_headers(pages:list[tuple[float,list[tuple[float,float,str,list[list[str]]|None]]]])->tuple[list[list[tuple[str,list[list[str]]|None]]],list[tuple[int,str]]]:
    counter=Counter()
    variants={}
    for height,blocks in pages:
        keys=set()
        for top,bottom,text,rows in blocks:
            if rows is None and on_edge(top,bottom,height):#table cant be header
                keys.add(edge_key(text))
                variants.setdefault(edge_key(text),set()).add(" ".join(text.split()).lower())
        counter.update(keys)
    repeated=set()
    if len(pages)>=3:#pdfs with less than 3 pages are not good candidates
        for key,count in counter.items():
            if count>=len(pages)/2:
                repeated.add(key)
    output=[]
    titles=[]
    seen=set()
    for page_index,(height,blocks) in enumerate(pages):#we pass again and remove the repeating ones
        kept_texts=[]
        for top,bottom,text,rows in blocks:
            if rows is not None or not on_edge(top,bottom,height) or not edge_key(text) in repeated:#we alway keep tables and not headers
                kept_texts.append((text,rows))
            elif len(variants[edge_key(text)])==1 and edge_key(text) not in seen:
                seen.add(edge_key(text))
                titles.append((page_index,text))
        output.append(kept_texts)
    return output,titles

def on_edge(top: float,bottom: float,height: float)->bool:
    return bottom<EDGE*height or top>(1-EDGE)*height

def table_at(block,tables: list[tuple[tuple,list[list[str]]]])->int|None:#to avoid duplicates from tables and normal text parsing
    x=(block[0]+block[2])/2
    y=(block[1]+block[3])/2
    for index,(box_bounds,rows) in enumerate(tables):
        if box_bounds[0]<=x<=box_bounds[2] and box_bounds[1]<=y<=box_bounds[3]:#if its inside the table bounding box
            return index
    return None

def is_table(rows: list[list[str]])->bool:
    cells=[]
    for row in rows:
        for cell in row:
            cells.append(cell)
    empty=0
    for cell in cells:
        if not cell.strip():
            empty+=1
    return empty<len(cells)/2 and len(rows)>=2 and len(rows[0])>=2

def merging_cell(pos:tuple[int,int],boxes,rows:list[list[str]])->str:
    row_index,col_index=pos
    column=[]
    for row_boxes in boxes:#for every row bounding box 
        if col_index<len(row_boxes) and row_boxes[col_index] is not None:#we keep those that are related to our merging cell
            column.append(row_boxes[col_index])
    row=[]
    for box in boxes[row_index]:
        if box is not None:#we keep the bounding boxes of the related row
            row.append(box)
    if not column or not row:#nothing to merge
        return ""
    narrow=min(column,key=lambda box:box[2]-box[0])#width
    low=min(row,key=lambda box:box[3]-box[1])#height
    x=(narrow[0]+narrow[2])/2#the middle of column col_index
    y=(low[1]+low[3])/2#the middle of row row_index
    for curr_row_index,row_boxes in enumerate(boxes):
        for curr_col_index,box in enumerate (row_boxes):
            if  box is not None and box[0]<=x<=box[2] and box[1]<=y<=box[3]:#if current box extends inside the one we want then its the merged one
                return rows[curr_row_index][curr_col_index]
    return ""

def filled_rows(table)->list[list[str]]:
    rows=[]
    for row in table.extract():
        new_row=[]
        for cell in row:
            new_row.append(cell or "")
        rows.append(new_row)
    boxes=[]
    for row in table.rows:
        boxes.append(row.cells)
    for row_index,row_boxes in enumerate(boxes):
        for col_index,box in enumerate(row_boxes):
            if box is None:
                rows[row_index][col_index]=merging_cell((row_index,col_index),boxes,rows)
    return rows

def page_tables(page,last:tuple[list[list[str]],float]|None)->tuple[list[tuple[tuple,list[list[str]]]],tuple[list[list[str]],float]|None]:
    height=page.rect.height
    tables=[]
    for table in page.find_tables(strategy="lines_strict").tables:
        rows=filled_rows(table)
        top=table.bbox[1]/height#percentage of the page
        if last is not None and top<TABLE_START and last[1]>TABLE_END and len(rows[0])==len(last[0][0]):#the table of this page is the continuation of a table in last page
            if rows[0]==last[0][0]:#if it repeats the header we skip it
                rows=rows[1:]
            last[0].extend(rows)
            tables.append((table.bbox,[]))
            last=(last[0],table.bbox[3]/height)
        elif is_table(rows):
            tables.append((table.bbox,rows))
            last=(rows,table.bbox[3]/height)
    if not tables:
        last=None
    return tables,last

def find_title(blocks:list[tuple[str,list[list[str]]|None]],title:str)->tuple[int,str,str]|None:
    key=title.lower()
    for index,(text,rows) in enumerate(blocks):
        if rows is not None:#its a table
            continue
        flat=" ".join(text.split())#all the text in a single line
        if flat.lower()==key:
            return index,flat,""
        if flat.lower().startswith(key+" "):
            return index,flat[:len(key)],flat[len(key):].strip()
    return None

def add_headings(pages:list[list[tuple[str,list[list[str]]|None]]],bookmarks:list,slides:bool)->dict[int,str]:
    titles={}
    on_top=[0]*len(pages)#for multiple bookmarks in the same page
    for level,title,page in bookmarks:
        page_index=page-1#-1 because bookmark counts from 1 and not 0
        title=" ".join(title.split())
        if not title or not 1<=page<=len(pages):#if page is not in pages then its outside link
            continue
        blocks=pages[page_index]
        found=find_title(blocks,title)
        if slides and page_index not in titles:
            if found is not None:
                index,written,rest=found
                title=written
                if rest:#if we have text we remove the title so we dont have duplicates
                    blocks[index]=(rest,None)
                else:
                    del blocks[index]
            titles[page_index]=title
        elif found is None:
            blocks.insert(on_top[page_index],("#"*min(level,6)+" "+title,None))#6 is the max depth
            on_top[page_index]+=1
        else:
            index,written,rest=found
            blocks[index]=("#"*min(level,6)+" "+written,None)
            if rest:
                blocks.insert(index+1,(rest,None))
    return titles

def is_suspisious(blocks)->bool:
    texts=[]
    for block in blocks:
        _,_,text,rows=block
        texts.append(text)
        if rows is None:
            continue
        for row in rows:
            for cell in row:
                texts.append(cell)
    words=WORD.findall(" ".join(texts))
    if not words:
        return False
    suspisious_words=0
    for word in words:
        suspisious_char=0
        for c in word:
            if "\u00c0"<=c<="\u00ff":
                suspisious_char+=1
        if suspisious_char>len(word)/2:
            suspisious_words+=1
    return suspisious_words>len(words)*SUSPISIOUS_WORD_RATIO

def fix_suspisious_text(text:str)->str:
    chars=[]
    for c in text:
        if not "\u00a0"<=c<="\u00ff":#its not broken
            chars.append(c)
            continue
        try:
            chars.append(bytes([ord(c)]).decode("cp1253"))
        except UnicodeDecodeError:#cp1253 does not have this
            try:
                chars.append(bytes([ord(c)]).decode("iso8859_7"))
            except UnicodeDecodeError:#iso8859_7 does not have this
                chars.append(c)
    return "".join(chars)

def fix_suspisious_blocks(blocks):
    for i,block in enumerate(blocks):
        y0,y1,text,rows=block
        new_text=fix_suspisious_text(text)
        if rows is None:
            blocks[i]=y0,y1,new_text,None
        else:
            new_rows=[]
            for row in rows:
                new_cells=[]
                for cell in row:
                    new_cells.append(fix_suspisious_text(cell))
                new_rows.append(new_cells)
            blocks[i]=y0,y1,new_text,new_rows