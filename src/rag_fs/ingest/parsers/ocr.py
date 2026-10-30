from ...config import OCRConfig
from pathlib import Path
import pytesseract
from PIL import Image, ImageOps
from .pages import drop_repeated_edges,fix_text_block,get_words,join_broken_pages
from .text import WORD,is_greek

LOOKALIKE = str.maketrans("ABEZHIKMNOPTYXoviukxap", "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧονιυκχαρ")
OCR_SAMPLE=10

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
    return fix_lookalikes(text,greek_words(text)),True

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

def ocr_image(image,ocr:OCRConfig)->tuple[str,float]:
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

def greek_words(text:str)->set[str]:#the words with a greek letter, to know how the text writes them
    words=set()
    for w in WORD.findall(text):
        w=w.lower()
        if any(is_greek(c) for c in w):
            words.add(w)
    return words

def fix_lookalikes(text:str,words:set[str])->str:
    return WORD.sub(lambda match: fix_word(match.group(0),words),text)#fixes greek words broken from ocr

def fix_word(word:str,words:set[str])->str:
    has_greek_char=False
    for c in word:
        if is_greek(c):
            has_greek_char=True
    greek=word.translate(LOOKALIKE)
    if greek.lower() in words or has_greek_char:
        return greek
    return word
