from ...config import OCRConfig
from pathlib import Path
import pytesseract
from PIL import Image, ImageOps
import numpy
import unicodedata
from .pages import drop_repeated_edges,fix_text_block,get_words,join_broken_pages
from collections import Counter
from .text import LETTERS,WORD,is_greek
import os

os.environ.setdefault("OMP_THREAD_LIMIT","1")

LOOKALIKE = str.maketrans("ABEZHIKMNOPTYXoviukxap", "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧονιυκχαρ")
OCR_SAMPLE=10
SKEW_STEP=0.5#degrees between the angles we try
SKEW_MAX=5#a photo of a page is rarely turned more than this
SKEW_WIDTH=1000#the picture is made this small to find the angle fast
SKEW_GAIN=0.2#a turn is used only when it makes the lines this much sharper than no turn
INK=[255 if value<128 else 0 for value in range(256)]#a table for point(): dark pixels become ink

def read_image(path:Path,ocr:OCRConfig)->tuple[str,bool]:
    with Image.open(path) as img:
        text_by_page=ocr_frames(img,frame_candidates(path,img,ocr),ocr)
    pages=[]
    for index in sorted(text_by_page):
        pages.append(text_by_page[index].split("\n\n"))
    paragraphs=clean_ocr_pages(pages)
    if not paragraphs:
        return "",False
    text="\n\n".join(paragraphs)
    return fix_lookalikes(text,word_counts(text)),True

def frame_candidates(path:Path,img:Image.Image,ocr:OCRConfig)->list[int]:#the frames big enough for ocr
    if path.suffix.lower() in (".tif", ".tiff"):
        frames_count=getattr(img, "n_frames", 1)#we keep multiple frames
    else:
        frames_count=1
    candidates=[]#for sampling
    for index in range(frames_count):
        img.seek(index)
        if min(img.size)<ocr.min_image_size:
            continue
        candidates.append(index)
    return candidates

def ocr_frames(img:Image.Image,candidates:list[int],ocr:OCRConfig)->dict[int,str]:
    text_by_page={}
    samples,rest=ocr_sample(candidates)
    for group in (samples,rest):
        if group is rest and not text_by_page:#if it failed with the samples we dont bother
            break
        for index in group:
            img.seek(index)
            text=run_ocr(prepare_frame(img),ocr)
            if text is None:
                continue
            text_by_page[index]=text
    return text_by_page

def prepare_frame(img:Image.Image)->Image.Image:#cleans up the image for better readability
    curr_frame=ImageOps.exif_transpose(img)
    if curr_frame.mode.startswith("I;16"):
        curr_frame=curr_frame.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    rgba = curr_frame.convert("RGBA")#we convert all images to white background with bold text so its more visible
    page = Image.new("RGB", rgba.size, "white")
    page.paste(rgba, mask=rgba.getchannel("A"))
    return page

def clean_ocr_pages(pages:list[list[str]])->list[str]:
    pages,titles=drop_repeated_edges(pages)#we remove headers that repeat
    for page_index,text in reversed(titles):#it keeps the correct order
        pages[page_index].insert(0,text)#we add the titles
    pages_normalized=[]
    for page in pages:#we normalize to match other file types
        temp=[]
        for paragraph in page:
            temp.append((paragraph,None))#get_words needs tuple with the rows in case its a table, but here is none
        pages_normalized.append(temp)
    words=get_words(pages_normalized)
    clean_ocr_result=[]
    for page in pages:
        for paragraph in page:
            paragraph=fix_text_block(paragraph,words)
            if not paragraph:
                continue
            if clean_ocr_result:#we have something before
                joined=join_broken_pages(clean_ocr_result[-1],paragraph,words)#we check if we have broken pages
                if joined is not None:
                    clean_ocr_result[-1]=joined#we combine them
                    continue
            clean_ocr_result.append(paragraph)
    return clean_ocr_result

def ocr_sample(candidates:list[int])->tuple[list[int],list[int]]:
    samples=[]
    rest=[]
    if len(candidates)<=OCR_SAMPLE:
        samples=candidates
        return samples,rest
    for i in range(OCR_SAMPLE):
        samples.append(candidates[i*len(candidates)//OCR_SAMPLE])
    for candidate in candidates:
        if candidate not in samples:
            rest.append(candidate)
    return samples,rest

def run_ocr(image,ocr:OCRConfig)->str|None:
    text_ocr,confidence=ocr_image(image,ocr)
    if confidence<ocr.min_confidence or len(text_ocr.strip())<ocr.min_chars:#ocr is not confident source
        return None
    return text_ocr

def skew_angle(image:Image.Image)->float:#turns the image a little bit to test if its clearer
    gray=image.convert("L")
    if gray.width>SKEW_WIDTH:
        gray=gray.resize((SKEW_WIDTH,max(1,gray.height*SKEW_WIDTH//gray.width)))
    ink=gray.point(INK)#dark pixels are ink
    best,best_score=0.0,-1.0
    straight_score=-1.0
    steps=round(SKEW_MAX/SKEW_STEP)
    for step in range(-steps,steps+1):
        angle=step*SKEW_STEP
        rows=numpy.asarray(ink.rotate(angle,fillcolor=0),dtype=numpy.float64).sum(axis=1)
        score=float(numpy.sum(numpy.diff(rows)**2))#straight lines give sharp steps between lines of text and the gaps
        if step==0:
            straight_score=score
        if score>best_score or (score==best_score and abs(angle)<abs(best)):
            best,best_score=angle,score
    if best_score<straight_score*(1+SKEW_GAIN):#its not better or it needs full rotate
        return 0.0
    return best

def straighten(image:Image.Image)->Image.Image:
    angle=skew_angle(image)
    if angle==0:
        return image
    return image.rotate(angle,expand=True,fillcolor="white")

def ocr_image(image,ocr:OCRConfig)->tuple[str,float]:
    image=straighten(image)
    original=parse_ocr_image(image,ocr)
    if original[1]>=ocr.min_confidence:
        return original
    rotated=parse_ocr_image(image.rotate(180),ocr)#we try to rotate to be read better
    if rotated[1]>original[1]:#we keep the best
        return rotated
    return original

def parse_ocr_image(image,ocr:OCRConfig)->tuple[str,float]:
    paragraphs={}
    confidences=[]
    data=pytesseract.image_to_data(image,"+".join(ocr.languages),tessdata_flag(ocr),output_type=pytesseract.Output.DICT)
    for i in range(len(data["text"])):
        if not data["text"][i].strip() or data["conf"][i]==-1:
            continue
        paragraphs.setdefault((data["block_num"][i],data["par_num"][i]),{}).setdefault(data["line_num"][i],[]).append(data["text"][i])
        confidences.append(data["conf"][i])
    blocks=[]
    for paragraph in paragraphs.values():
        lines=[]
        for line in paragraph.values():
            lines.append(" ".join(line))
        blocks.append("\n".join(lines))
    output="\n\n".join(blocks)
    if len(confidences)==0:
        mean=0
    else:
        mean=sum(confidences)/len(confidences)
    return output,mean

def tessdata_flag(ocr:OCRConfig)->str:#where tesseract finds its languages
    if ocr.tessdata_dir:
        return f'--tessdata-dir "{Path(ocr.tessdata_dir).expanduser()}"'
    return ""

def check_ocr(ocr: OCRConfig):
    if not ocr.languages:
        raise ValueError("No OCR language in config")
    if ocr.tessdata_dir:
        path=Path(ocr.tessdata_dir).expanduser()
        if not path.is_dir():
            raise ValueError(f"No OCR data folder with name {path}")
    installed_lang=pytesseract.get_languages(tessdata_flag(ocr))
    if installed_lang:
        for lang in ocr.languages:
            if lang not in installed_lang:
                raise ValueError(f"{lang} is not installed for OCR")
    else:
        raise ValueError("OCR has none installed languages")

def word_counts(text:str)->Counter:
    return Counter(w.lower() for w in LETTERS.findall(text))

def plain(word:str)->str:
    return "".join(c for c in unicodedata.normalize("NFD",word) if not unicodedata.combining(c))

def fix_lookalikes(text:str,greek:Counter,latin:Counter|None=None)->str:
    if latin is None:
        latin=Counter()
    plain_greek=Counter()
    for word,count in greek.items():
        plain_greek[plain(word)]+=count
    return WORD.sub(lambda match: fix_word(match.group(0),greek,latin,plain_greek),text)#fixes greek words broken from ocr

def fix_word(word:str,greek:Counter,latin:Counter,plain_greek:Counter|None=None)->str:
    has_greek_char=False
    for c in word:
        if is_greek(c):
            has_greek_char=True
    greek_word=word.translate(LOOKALIKE)
    if has_greek_char:#a greek word with a latin letter inside
        return greek_word
    count=greek[greek_word.lower()]
    if count==0 and plain_greek is not None:
        count=plain_greek[plain(greek_word.lower())]
    if count>0 and count>=latin[word.lower()]:#the text writes it in greek at least as often as in latin
        return greek_word
    return word
