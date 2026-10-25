import numpy
import pymupdf
import pytesseract
import pytest
from PIL import Image

from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.parsers import ocr as ocr_module
from rag_fs.ingest.parsers import parse_file
from rag_fs.ingest.parsers.ocr import check_ocr, ocr_image, read_image


# a white picture with the html written on it, like a scanned page
def picture(html, width=1000, height=300, mode="RGB"):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    if html:
        page.insert_htmlbox(pymupdf.Rect(30, 30, width - 30, height - 30), f"<div style='font-size:22px'>{html}</div>")
    pix = page.get_pixmap(dpi=200)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert(mode)


# (text written on the picture, text that comes out)
READ_CASES = {
    "english": ("The deadline is strict.", "The deadline is strict."),
    "greek with accents": ("Οι εργασίες παραδίδονται στις 12 Μαρτίου.", "Οι εργασίες παραδίδονται στις 12 Μαρτίου."),
    "greek and english in one line": ("Οι ασκήσεις του εργαστηρίου είναι σε Java.", "Οι ασκήσεις του εργαστηρίου είναι σε Java."),
    "lines of a paragraph": ("Πρώτη σειρά<br>δεύτερη σειρά", "Πρώτη σειρά\nδεύτερη σειρά"),
    "two paragraphs": ("<p>Πρώτη παράγραφος</p><p style='margin-top:60px'>Δεύτερη παράγραφος</p>",
                       "Πρώτη παράγραφος\n\nΔεύτερη παράγραφος"),
}


# the text of the picture comes out with its lines and paragraphs
@pytest.mark.parametrize("html, expected", READ_CASES.values(), ids=READ_CASES.keys())
def test_ocr_image_reads_the_text(html, expected):
    text, confidence = ocr_image(picture(html), OCRConfig())
    assert text == expected
    assert confidence >= 80


# a picture with nothing on it gives no text and no confidence
def test_ocr_image_of_a_blank_picture():
    assert ocr_image(picture(""), OCRConfig()) == ("", 0.0)


# a grey picture is read like a colour one
def test_ocr_image_of_a_grey_picture():
    html, expected = READ_CASES["greek with accents"]
    assert ocr_image(picture(html, mode="L"), OCRConfig())[0] == expected


# the languages come from the config: without Greek the Greek words are lost
def test_ocr_image_uses_the_languages_of_the_config():
    text, _ = ocr_image(picture("Οι εργασίες παραδίδονται"), OCRConfig(languages=["eng"]))
    assert "εργασίες" not in text


# the models of the system (empty tessdata_dir) read the text too
def test_ocr_image_with_the_system_models():
    text, _ = ocr_image(picture("The deadline is strict."), OCRConfig(tessdata_dir=""))
    assert text == "The deadline is strict."


# the models come from the folder of the config: a folder without models cannot read
def test_ocr_image_uses_the_folder_of_the_config(tmp_path):
    with pytest.raises(pytesseract.TesseractError):
        ocr_image(picture("The deadline is strict."), OCRConfig(tessdata_dir=str(tmp_path)))


# the same picture always gives the same text
def test_ocr_image_is_deterministic():
    image = picture(READ_CASES["two paragraphs"][0])
    assert ocr_image(image, OCRConfig()) == ocr_image(image, OCRConfig())


# the default config and the system models are ready to use
@pytest.mark.parametrize("folder", ["~/.local/share/tessdata_best", ""], ids=["best models", "system models"])
def test_check_ocr_accepts_installed_languages(folder):
    check_ocr(OCRConfig(tessdata_dir=folder))


# a language or a folder that is not there stops before any file is read, and the message names it
@pytest.mark.parametrize("config, named", [
    pytest.param(dict(languages=["ell", "xyz"]), "xyz", id="missing language"),
    pytest.param(dict(tessdata_dir="~/no/such/folder"), "no/such/folder", id="missing folder"),
    pytest.param(dict(languages=[]), "language", id="no language"),
])
def test_check_ocr_rejects(config, named):
    with pytest.raises(ValueError, match=named):
        check_ocr(OCRConfig(**config))


# a folder that exists but has no models is rejected too
def test_check_ocr_rejects_an_empty_folder(tmp_path):
    with pytest.raises(ValueError, match="ell|language"):
        check_ocr(OCRConfig(tessdata_dir=str(tmp_path)))


# tesseract can give a box with only a space and a good confidence: it is not a word
def test_ocr_image_skips_boxes_without_text(monkeypatch):
    boxes = {"text": ["", " ", "Λέξη"], "conf": [-1, 95, 90], "block_num": [1, 1, 1], "par_num": [1, 1, 1], "line_num": [1, 1, 1]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *args, **kwargs: boxes)
    assert ocr_image(picture(""), OCRConfig()) == ("Λέξη", 90.0)


# ---------- step O2: image files through parse_file ----------

SENTENCE = READ_CASES["greek with accents"][0]


# saves the picture as a file of the corpus and parses it
def parse_picture(tmp_path, name, image, ocr=None, **save):
    root = tmp_path / "corpus"
    root.mkdir()
    image.save(root / name, **save)
    scanned = scan(CorpusConfig(roots=[root])).files[0]
    return parse_file(scanned) if ocr is None else parse_file(scanned, ocr)


# every image format is read by OCR and gets the status ocr
@pytest.mark.parametrize("name", ["page.png", "page.jpg", "page.jpeg", "page.bmp", "page.gif", "page.webp", "page.tif",
                                  "page.tiff", "PAGE.PNG"])
def test_parse_file_reads_image_files(tmp_path, name):
    doc = parse_picture(tmp_path, name, picture(SENTENCE))
    assert (doc.parse_status, doc.content_type, doc.raw_text) == ("ocr", "prose", SENTENCE)


# the ell model writes µ for μ, parse_file turns it back (D69)
def test_parse_file_fixes_the_micro_sign_of_ocr(tmp_path):
    text = "Το εργαστήριο του μαθήματος είναι σε Java."
    assert parse_picture(tmp_path, "lab.png", picture(text)).raw_text == text


# the OCR settings given to parse_file are used: without Greek the Greek words are lost
def test_parse_file_passes_the_ocr_config(tmp_path):
    doc = parse_picture(tmp_path, "page.png", picture(SENTENCE), OCRConfig(languages=["eng"]))
    assert "εργασίες" not in doc.raw_text


# pictures with no text, or with nothing readable, are empty
def test_parse_file_blank_picture_is_empty(tmp_path):
    assert parse_picture(tmp_path, "blank.png", picture("")).parse_status == "empty"


# a broken image file is failed, like any broken file
def test_parse_file_broken_image_is_failed(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    assert parse_file(scan(CorpusConfig(roots=[root])).files[0]).parse_status == "failed"


# (what ocr_image gives, settings, status after parse_file)
FILTER_CASES = {
    "good text": (("Μια πρόταση με αρκετό κείμενο", 90.0), {}, "ocr"),
    "confidence below the limit": (("Μια πρόταση με αρκετό κείμενο", 59.9), {}, "empty"),
    "confidence at the limit is kept": (("Μια πρόταση με αρκετό κείμενο", 60.0), {}, "ocr"),
    "too few characters": (("ΟΚ", 95.0), {}, "empty"),
    "exactly min_chars is kept": (("α" * 20, 95.0), {}, "ocr"),
    "one less than min_chars": (("α" * 19, 95.0), {}, "empty"),
    "spaces do not count as characters": (("α" * 19 + "     ", 95.0), {}, "empty"),
    "limits come from the config": (("ΟΚ", 40.0), {"min_confidence": 30, "min_chars": 2}, "ocr"),
}


# the D30 quality limits decide if the text is kept
@pytest.mark.parametrize("result, settings, status", FILTER_CASES.values(), ids=FILTER_CASES.keys())
def test_parse_file_quality_limits(tmp_path, monkeypatch, result, settings, status):
    monkeypatch.setattr(ocr_module, "ocr_image", lambda image, ocr: result)
    doc = parse_picture(tmp_path, "page.png", picture(SENTENCE), OCRConfig(**settings))
    assert doc.parse_status == status


# small pictures (icons, logos) are not even sent to OCR
@pytest.mark.parametrize("size, settings, status", [
    pytest.param((299, 1000), {}, "empty", id="short side under 300"),
    pytest.param((1000, 299), {}, "empty", id="other side under 300"),
    pytest.param((300, 300), {}, "ocr", id="exactly 300"),
    pytest.param((120, 120), {"min_image_size": 100}, "ocr", id="limit from the config"),
])
def test_parse_file_small_pictures(tmp_path, monkeypatch, size, settings, status):
    monkeypatch.setattr(ocr_module, "ocr_image", lambda image, ocr: ("Μια πρόταση με αρκετό κείμενο", 90.0))
    doc = parse_picture(tmp_path, "icon.png", Image.new("RGB", size, "white"), OCRConfig(**settings))
    assert doc.parse_status == status


# read_image says if OCR ran: a small picture is not read at all
def test_read_image_says_if_ocr_ran(tmp_path):
    big, small = tmp_path / "big.png", tmp_path / "small.png"
    picture(SENTENCE).save(big)
    Image.new("RGB", (50, 50), "white").save(small)
    assert read_image(big, OCRConfig()) == (SENTENCE, True)
    assert read_image(small, OCRConfig()) == ("", False)


# black text on a see-through background: without a white background it is black on black
def transparent():
    text = picture(SENTENCE, mode="L")
    image = Image.new("RGBA", text.size, (0, 0, 0, 0))
    image.putalpha(text.point(lambda v: 255 - v))
    return image


# grey text (100 of 255) in 16 bits: cut to 8 bits instead of scaled, it turns white and is lost
def grey_16bit():
    grey = numpy.array(picture(SENTENCE, mode="L"), dtype=numpy.uint16)
    return (255 - (255 - grey) * 155 // 255) * 257


# (picture, how it is saved) for colour modes that tesseract cannot take as they are
MODE_CASES = {
    "transparent background": (transparent, "page.png", {}),
    "palette": (lambda: picture(SENTENCE).convert("P"), "page.png", {}),
    "palette with transparency": (lambda: transparent().convert("P"), "page.png", {"transparency": 0}),
    "cmyk jpeg": (lambda: picture(SENTENCE).convert("CMYK"), "page.jpg", {}),
    "16-bit grey": (lambda: Image.fromarray(grey_16bit()), "page.png", {}),
    "16-bit grey, big endian": (lambda: Image.frombytes("I;16B", picture("").size, grey_16bit().astype(">u2").tobytes()),
                                "page.tif", {}),
    "black and white": (lambda: picture(SENTENCE).convert("1"), "page.png", {}),
}


# every colour mode ends up as dark text on white
@pytest.mark.parametrize("make, name, save", MODE_CASES.values(), ids=MODE_CASES.keys())
def test_parse_file_reads_every_colour_mode(tmp_path, make, name, save):
    assert parse_picture(tmp_path, name, make(), **save).raw_text == SENTENCE


# a phone photo saved sideways with an EXIF note to turn it is read the right way up
def test_parse_file_turns_photos_like_the_exif_says(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: turn 90 degrees clockwise to show
    sideways = picture(SENTENCE).rotate(90, expand=True)
    assert parse_picture(tmp_path, "photo.jpg", sideways, exif=exif).raw_text == SENTENCE


# a scanned tiff keeps every page, a gif only its first frame
@pytest.mark.parametrize("name, expected", [
    ("scan.tif", "Οι εργασίες της πρώτης σελίδας\n\nΟι ασκήσεις της δεύτερης σελίδας"),
    ("moving.gif", "Οι εργασίες της πρώτης σελίδας"),
])
def test_parse_file_pictures_with_many_frames(tmp_path, name, expected):
    first, second = picture("Οι εργασίες της πρώτης σελίδας"), picture("Οι ασκήσεις της δεύτερης σελίδας")
    assert parse_picture(tmp_path, name, first, save_all=True, append_images=[second]).raw_text == expected


# a small page in the middle of a scan is skipped, the pages after it are still read
def test_parse_file_tiff_with_a_small_page(tmp_path):
    first, last = picture("Οι εργασίες της πρώτης σελίδας"), picture("Οι ασκήσεις της δεύτερης σελίδας")
    doc = parse_picture(tmp_path, "scan.tif", first, save_all=True, append_images=[Image.new("RGB", (100, 100), "white"), last])
    assert doc.raw_text == "Οι εργασίες της πρώτης σελίδας\n\nΟι ασκήσεις της δεύτερης σελίδας"
