import sqlite3
from pathlib import Path

import pytest

from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.models import Document

GREEK = "Σημειώσεις του μαθήματος για την εξέταση."
SHA = "ab" * 32
OTHER_SHA = "cd" * 32


def doc(text=GREEK, status="ok", content_type="prose", ext=".txt", sha=SHA):
    return Document(file_path=Path("notes/a" + ext), root_id="root_0", ext=ext, raw_text=text,
                    last_modified=100.0, content_type=content_type, parse_status=status, sha256=sha)


# another file with the same bytes: other path, root and date
def same_bytes(ext=".txt", sha=SHA):
    return ScannedFile(root_id="root_1", abs_path=Path("/elsewhere/copy" + ext), rel_path=Path("copy" + ext),
                       size=1, mtime=200.0, sha256=sha)


def cache(tmp_path):
    from rag_fs.ingest.cache import open_cache
    return open_cache(tmp_path / "data" / "cache.db")


# what the cache gives back for a file: the text, status and type are stored, the path, root and date are the file's
def test_cache_gives_the_text_for_another_file_with_the_same_bytes(tmp_path):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc())
    assert get_cached(db, same_bytes()) == Document(
        file_path=Path("copy.txt"), root_id="root_1", ext=".txt", raw_text=GREEK, last_modified=200.0,
        content_type="prose", parse_status="ok", sha256=SHA)


# a file the cache has not seen: other bytes, or the same bytes with another extension (.md is read differently)
@pytest.mark.parametrize("scanned", [same_bytes(sha=OTHER_SHA), same_bytes(ext=".md")], ids=["other bytes", "other extension"])
def test_cache_miss(tmp_path, scanned):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc())
    assert get_cached(db, scanned) is None


def test_cache_empty_is_a_miss(tmp_path):
    from rag_fs.ingest.cache import get_cached
    assert get_cached(cache(tmp_path), same_bytes()) is None


# what took work to read is kept; a failure is tried again next time, and a binary file needs no reading
@pytest.mark.parametrize("status, content_type, text, kept", [
    ("ok", "prose", GREEK, True),
    ("ocr", "prose", GREEK, True),
    ("empty", "prose", "", True),
    ("ok", "code", "def main():\n    pass\n", True),
    ("ok", "tabular", "name: Maria; grade: 9", True),
    ("failed", "prose", "", False),
    ("unsupported", "binary", "", False),
])
def test_cache_keeps_what_was_read(tmp_path, status, content_type, text, kept):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc(text, status, content_type))
    found = get_cached(db, same_bytes())
    if kept:
        assert found is not None
        assert (found.raw_text, found.parse_status, found.content_type) == (text, status, content_type)
    else:
        assert found is None


# the text comes back exactly, whatever it has inside
@pytest.mark.parametrize("text", [
    "a\x00b", "line\r\nline\rline", "  spaces around  \n", chr(0x1F600) + " " + chr(0x3a9) + chr(0x2014),
    "'quotes' \"double\" ; DROP TABLE x; --", "x" * 3_000_000,
], ids=["nul", "line ends", "spaces", "emoji", "sql characters", "big"])
def test_cache_keeps_the_text_exactly(tmp_path, text):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc(text))
    found = get_cached(db, same_bytes())
    assert found is not None and found.raw_text == text


# a file read again replaces what was there
def test_cache_newest_text_wins(tmp_path):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc("old"))
    put_cached(db, doc("new", status="ocr"))
    found = get_cached(db, same_bytes())
    assert found is not None and (found.raw_text, found.parse_status) == ("new", "ocr")


# files with other bytes do not touch each other
def test_cache_keeps_files_apart(tmp_path):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc("first"))
    put_cached(db, doc("second", sha=OTHER_SHA))
    put_cached(db, doc("# third", ext=".md"))
    texts = [get_cached(db, s) for s in (same_bytes(), same_bytes(sha=OTHER_SHA), same_bytes(ext=".md"))]
    assert [t.raw_text if t else None for t in texts] == ["first", "second", "# third"]


# each file is saved as soon as it is put: a run stopped with Ctrl+C keeps what it read
def test_cache_is_saved_after_every_file(tmp_path):
    from rag_fs.ingest.cache import get_cached, open_cache, put_cached
    path = tmp_path / "data" / "cache.db"
    db = open_cache(path)
    put_cached(db, doc())
    other = open_cache(path)#another program opens it while the first is still running
    found = get_cached(other, same_bytes())
    assert found is not None and found.raw_text == GREEK


# the next run finds what the last one saved
def test_cache_is_there_on_the_next_run(tmp_path):
    from rag_fs.ingest.cache import get_cached, open_cache, put_cached
    path = tmp_path / "data" / "cache.db"
    db = open_cache(path)
    put_cached(db, doc())
    db.close()
    found = get_cached(open_cache(path), same_bytes())
    assert found is not None and found.raw_text == GREEK


# the folders of the cache are made if they are missing
def test_open_cache_makes_the_folders(tmp_path):
    from rag_fs.ingest.cache import open_cache
    path = tmp_path / "a" / "b" / "cache.db"
    assert isinstance(open_cache(path), sqlite3.Connection)
    assert path.is_file()


# the text is stored compressed: a big corpus must not need a second copy of all its text on the disk
def test_cache_text_is_compressed(tmp_path):
    from rag_fs.ingest.cache import open_cache, put_cached
    path = tmp_path / "data" / "cache.db"
    db = open_cache(path)
    text = (GREEK + "\n") * 50_000#4 MB of text in utf-8
    put_cached(db, doc(text))
    db.close()
    assert path.stat().st_size < len(text.encode("utf-8")) // 10


# --rebuild: everything, or only some extensions
@pytest.mark.parametrize("exts, left", [(None, []), ([".rtf"], [".txt", ".md"]), ([".rtf", ".md"], [".txt"]), ([], [".txt", ".rtf", ".md"])],
                         ids=["all", "one extension", "two extensions", "none"])
def test_drop_cached(tmp_path, exts, left):
    from rag_fs.ingest.cache import drop_cached, get_cached, put_cached
    db = cache(tmp_path)
    for ext in (".txt", ".rtf", ".md"):
        put_cached(db, doc("text", ext=ext))
    drop_cached(db, exts)
    assert [ext for ext in (".txt", ".rtf", ".md") if get_cached(db, same_bytes(ext=ext))] == left


# NOTES.TXT and notes.txt are read the same way: the extension of the key is in small letters, as in parse_file
def test_cache_upper_case_extension(tmp_path):
    from rag_fs.ingest.cache import get_cached, put_cached
    db = cache(tmp_path)
    put_cached(db, doc())
    found = get_cached(db, ScannedFile(root_id="root_0", abs_path=Path("/c/COPY.TXT"), rel_path=Path("COPY.TXT"),
                                        size=1, mtime=1.0, sha256=SHA))
    assert found is not None and (found.raw_text, found.ext, found.file_path) == (GREEK, ".txt", Path("COPY.TXT"))


# what --rebuild dropped is gone for good, also for another program that opens the cache
def test_drop_cached_is_saved(tmp_path):
    from rag_fs.ingest.cache import drop_cached, get_cached, open_cache, put_cached
    path = tmp_path / "data" / "cache.db"
    db = open_cache(path)
    put_cached(db, doc())
    drop_cached(db, [".txt"])
    assert get_cached(open_cache(path), same_bytes()) is None


# piece C5: the manifest, path -> (size, date, sha256) of the last scan (D95)


def scanned(path, size=5, mtime=100.5, sha=SHA):
    return ScannedFile(root_id="root_0", abs_path=Path(path), rel_path=Path(Path(path).name), size=size, mtime=mtime, sha256=sha)


# what is saved comes back, by the whole path
def test_manifest_round_trip(tmp_path):
    from rag_fs.ingest.cache import load_manifest, save_manifest
    db = cache(tmp_path)
    save_manifest(db, [scanned("/c/a.txt"), scanned("/c/Σημειώσεις/b.txt", size=7, mtime=1.25, sha=OTHER_SHA)])
    assert load_manifest(db) == {Path("/c/a.txt"): (5, 100.5, SHA), Path("/c/Σημειώσεις/b.txt"): (7, 1.25, OTHER_SHA)}


def test_manifest_empty(tmp_path):
    from rag_fs.ingest.cache import load_manifest
    assert load_manifest(cache(tmp_path)) == {}


# a file saved again replaces what was there; the others stay
def test_manifest_newest_wins(tmp_path):
    from rag_fs.ingest.cache import load_manifest, save_manifest
    db = cache(tmp_path)
    save_manifest(db, [scanned("/c/a.txt"), scanned("/c/b.txt")])
    save_manifest(db, [scanned("/c/a.txt", size=9, mtime=2.0, sha=OTHER_SHA)])
    assert load_manifest(db) == {Path("/c/a.txt"): (9, 2.0, OTHER_SHA), Path("/c/b.txt"): (5, 100.5, SHA)}


# the date keeps all its digits: a change of a fraction of a second is a change
def test_manifest_keeps_the_exact_date(tmp_path):
    from rag_fs.ingest.cache import load_manifest, save_manifest
    db = cache(tmp_path)
    save_manifest(db, [scanned("/c/a.txt", mtime=1730563200.123456789)])
    assert load_manifest(db)[Path("/c/a.txt")][1] == 1730563200.123456789


# it is saved for the next run, and for another program while this one runs
def test_manifest_is_saved(tmp_path):
    from rag_fs.ingest.cache import load_manifest, open_cache, save_manifest
    path = tmp_path / "data" / "cache.db"
    save_manifest(open_cache(path), [scanned("/c/a.txt")])
    assert load_manifest(open_cache(path)) == {Path("/c/a.txt"): (5, 100.5, SHA)}


# --rebuild drops texts, not the manifest: the files did not change, the parsers did
def test_drop_cached_keeps_the_manifest(tmp_path):
    from rag_fs.ingest.cache import drop_cached, load_manifest, save_manifest
    db = cache(tmp_path)
    save_manifest(db, [scanned("/c/a.txt")])
    drop_cached(db, None)
    assert load_manifest(db) == {Path("/c/a.txt"): (5, 100.5, SHA)}
