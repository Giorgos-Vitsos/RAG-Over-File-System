import hashlib
import io
import os
import time
import zipfile
from pathlib import Path

import pytest

from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest import pipeline
from rag_fs.ingest.cache import drop_cached, get_cached, open_cache
from rag_fs.ingest.file_scanner import ScannedFile
from rag_fs.ingest.parsers import OCR_PARSERS, PARSERS
from rag_fs.ingest.parsers.text import read_text
from rag_fs.ingest.file_scanner import scan
from rag_fs.ingest.pipeline import read_files


# the whole corpus: what the scanner finds, read
def read_all(corpus, ocr=OCRConfig(), **settings):
    return read_files(scan(corpus).files, corpus, ocr, **settings)

NOTES = "Σημειώσεις του μαθήματος για την εξέταση."
CODE = "def main():\n    print('γεια')\n"


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in files.items():
            z.writestr(name, text if isinstance(text, bytes) else text.encode("utf-8"))
    return buffer.getvalue()


# a corpus of {name: text or bytes} read whole; (path, status, text) of every document in the order they come
def read(tmp_path, files, **settings):
    root = tmp_path / "corpus"
    for name, data in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    docs = read_all(CorpusConfig(roots=[root], **settings))
    return [(str(d.file_path), d.parse_status, d.raw_text) for d in docs]


# every file of the corpus gives a document, and an archive gives the documents of the files inside it
def test_read_files_files_and_archives(tmp_path):
    files = {"notes.txt": NOTES, "src/main.py": CODE, "work.zip": zip_bytes({"a/notes.txt": "Άλλες σημειώσεις."})}
    assert sorted(read(tmp_path, files)) == [
        ("notes.txt", "ok", NOTES), ("src/main.py", "ok", CODE), ("work.zip/a/notes.txt", "ok", "Άλλες σημειώσεις.")]


# a file inside an archive that is also outside is read once, from outside, even when the archive comes first
def test_read_files_copy_outside_wins(tmp_path):
    files = {"a.zip": zip_bytes({"notes.txt": NOTES, "main.py": CODE}), "z.txt": NOTES}
    assert read(tmp_path, files) == [("a.zip/main.py", "ok", CODE), ("z.txt", "ok", NOTES)]


# the same archive twice is read once
def test_read_files_same_archive_twice(tmp_path):
    data = zip_bytes({"notes.txt": NOTES})
    assert read(tmp_path, {"a.zip": data, "b/copy.zip": data}) == [("a.zip/notes.txt", "ok", NOTES)]


# an archive deeper than the limit, counted from the archive of the corpus, is failed
def test_read_files_depth_of_archives(tmp_path):
    files = {"work.zip": zip_bytes({"notes.txt": NOTES}), "main.py": CODE}
    assert read(tmp_path, files, max_archive_depth=0) == [("main.py", "ok", CODE), ("work.zip", "failed", "")]


# the OCR settings are checked before anything is read
def test_read_files_checks_ocr(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "notes.txt").write_text(NOTES, encoding="utf-8")
    with pytest.raises(ValueError):
        read_all(CorpusConfig(roots=[root]), OCRConfig(languages=[]))


# the same broken archive twice is reported once
def test_read_files_same_broken_archive_twice(tmp_path):
    assert read(tmp_path, {"a.zip": NOTES, "b.zip": NOTES}) == [("a.zip", "failed", "")]


# piece C2: read_files with the cache (D95)
# the readers of .txt and .md count the files they really read
@pytest.fixture
def reads(monkeypatch):
    names = []
    for ext, reader in ((".txt", PARSERS[".txt"]), (".md", PARSERS[".md"])):
        def counted(path, reader=reader):
            names.append(path.name)
            return reader(path)
        monkeypatch.setitem(PARSERS, ext, counted)
    return names


def write(root, files):
    for name, data in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


# one run over the corpus with the cache: (path, status, text) of every document
def run(root, db):
    return [(str(d.file_path), d.parse_status, d.raw_text) for d in read_all(CorpusConfig(roots=[root]), db=db)]


@pytest.fixture
def db(tmp_path):
    return open_cache(tmp_path / "data" / "cache.db")


# the second run reads nothing and gives the same documents
def test_second_run_reads_nothing(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "a/b.md": "# Title\n\nText.", "main.py": CODE})
    first = run(root, db)
    assert sorted(reads) == ["b.md", "notes.txt"]
    reads.clear()
    assert run(root, db) == first
    assert reads == []


# the same documents with and without the cache, field by field
def test_cached_documents_are_the_same(tmp_path, db):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "work.zip": zip_bytes({"a/in.txt": "Μέσα."})})
    plain = read_all(CorpusConfig(roots=[root]))
    read_all(CorpusConfig(roots=[root]), db=db)
    assert read_all(CorpusConfig(roots=[root]), db=db) == plain


# a file that changed is read again, the others not
def test_changed_file_is_read_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "other.txt": "Άλλο."})
    run(root, db)
    reads.clear()
    write(root, {"notes.txt": "Νέες σημειώσεις."})
    assert run(root, db) == [("notes.txt", "ok", "Νέες σημειώσεις."), ("other.txt", "ok", "Άλλο.")]
    assert reads == ["notes.txt"]


# a file that moved is not read again, and its document has the new path
def test_moved_file_is_not_read_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    run(root, db)
    reads.clear()
    (root / "notes.txt").rename(root / "moved.txt")
    assert run(root, db) == [("moved.txt", "ok", NOTES)]
    assert reads == []


# the same file in two places is read once and gives two documents, one for each path (D95)
def test_same_file_twice_is_read_once(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"a/notes.txt": NOTES, "b/copy.txt": NOTES})
    assert run(root, db) == [("a/notes.txt", "ok", NOTES), ("b/copy.txt", "ok", NOTES)]
    assert len(reads) == 1


# the same bytes with another extension are read again: .md is read differently
def test_same_bytes_other_extension_is_read(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": "# Title", "notes.md": "# Title"})
    run(root, db)
    assert sorted(reads) == ["notes.md", "notes.txt"]


# the files inside an archive go through the cache too: the archive is opened again, its files are not read again
def test_files_inside_archives_are_cached(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"work.zip": zip_bytes({"a/notes.txt": NOTES})})
    first = run(root, db)
    assert first == [("work.zip/a/notes.txt", "ok", NOTES)]
    reads.clear()
    assert run(root, db) == first
    assert reads == []


# a failure is not kept: the next run tries again (the parser may be fixed by then)
def test_failed_file_is_tried_again(tmp_path, db, monkeypatch):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    def broken(path):
        raise ValueError("broken")
    monkeypatch.setitem(PARSERS, ".txt", broken)
    assert run(root, db) == [("notes.txt", "failed", "")]
    monkeypatch.setitem(PARSERS, ".txt", read_text)
    assert run(root, db) == [("notes.txt", "ok", NOTES)]


# --rebuild .txt: only the .txt files are read again
def test_rebuild_reads_the_dropped_extensions_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "b.md": "# Title"})
    run(root, db)
    reads.clear()
    drop_cached(db, [".txt"])
    run(root, db)
    assert reads == ["notes.txt"]


# what one run read is in the cache for the next one, also when the next run opens the cache again
def test_cache_survives_between_runs(tmp_path, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    path = tmp_path / "data" / "cache.db"
    run(root, open_cache(path))
    reads.clear()
    assert run(root, open_cache(path)) == [("notes.txt", "ok", NOTES)]
    assert reads == []


# without a cache read_files reads everything, as before
def test_without_cache_everything_is_read(tmp_path, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    read_all(CorpusConfig(roots=[root]))
    read_all(CorpusConfig(roots=[root]))
    assert reads == ["notes.txt", "notes.txt"]


# piece C3: worker processes at low priority, cheap files first (D74)
FORK_WARNING = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")


# the workers start with "forkserver": a clean process, safe when this one runs threads (numpy does)
def test_workers_start_with_forkserver():
    assert pipeline.START_METHOD == "forkserver"


# the readers of .txt, .md and .pdf write the names of the files they really read in a file,
# because they run in other processes. A clean process would not have these readers,
# so these tests start the workers with fork, which copies this process as it is
@pytest.fixture
def logged(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    log = tmp_path / "reads.log"
    log.touch()
    def logger(reader):
        def logged_reader(path, *args):
            with open(log, "a", encoding="utf-8") as f:
                f.write(path.name + "\n")
            return reader(path, *args)
        return logged_reader
    monkeypatch.setitem(PARSERS, ".txt", logger(PARSERS[".txt"]))
    monkeypatch.setitem(PARSERS, ".md", logger(PARSERS[".md"]))
    monkeypatch.setitem(OCR_PARSERS, ".pdf", logger(lambda path, ocr: ("Κείμενο από OCR.", True)))
    return lambda: log.read_text(encoding="utf-8").split()


MIXED = {
    "notes.txt": NOTES, "a/b.md": "# Title\n\nText.", "main.py": CODE, "scan.pdf": b"%PDF-1.4 fake",
    "copy/notes.txt": NOTES, "work.zip": zip_bytes({"in/inside.txt": "Μέσα.", "in/main.py": CODE}),
    "broken.zip": "not a zip", "empty.txt": "", "image.class": b"\xca\xfe\xba\xbe",
}


def docs_of(root, **settings):
    return read_all(CorpusConfig(roots=[root]), **settings)


# with workers (started the normal way, with the real readers) the documents are the same, in the same order
@pytest.mark.filterwarnings("error::DeprecationWarning")
@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("with_cache", [False, True], ids=["no cache", "cache"])
def test_workers_give_the_same_documents(tmp_path, workers, with_cache):
    root = tmp_path / "corpus"
    write(root, MIXED)
    plain = docs_of(root)
    db = open_cache(tmp_path / "data" / "cache.db") if with_cache else None
    assert docs_of(root, db=db, workers=workers) == plain


# the same with the test readers, which also give an OCR document
@FORK_WARNING
def test_workers_give_the_same_documents_with_ocr(tmp_path, logged):
    root = tmp_path / "corpus"
    write(root, MIXED)
    plain = docs_of(root)
    assert ("scan.pdf", "ocr") in [(str(d.file_path), d.parse_status) for d in plain]
    assert docs_of(root, workers=2) == plain


# with workers the cache works as before: the second run reads nothing
@FORK_WARNING
def test_workers_use_the_cache(tmp_path, db, logged):
    root = tmp_path / "corpus"
    write(root, MIXED)
    first = docs_of(root, db=db, workers=2)
    read_once = logged()
    assert docs_of(root, db=db, workers=2) == first
    assert logged() == read_once


# the same file twice is read once, also when two workers could read it at the same time (D95)
@FORK_WARNING
@pytest.mark.parametrize("with_cache", [False, True], ids=["no cache", "cache"])
def test_workers_read_the_same_file_once(tmp_path, logged, with_cache):
    root = tmp_path / "corpus"
    write(root, {f"{i}/notes.txt": NOTES for i in range(6)})
    db = open_cache(tmp_path / "data" / "cache.db") if with_cache else None
    docs = docs_of(root, db=db, workers=4)
    assert [(str(d.file_path), d.raw_text) for d in docs] == [(f"{i}/notes.txt", NOTES) for i in range(6)]
    assert logged() == ["notes.txt"]


# a copy in another root of the corpus keeps its own root, path and date
@FORK_WARNING
def test_workers_copy_in_another_root(tmp_path, logged):
    first, second = tmp_path / "first", tmp_path / "second"
    write(first, {"notes.txt": NOTES})
    write(second, {"b/copy.txt": NOTES})
    os.utime(second / "b" / "copy.txt", (1_000_000, 1_000_000))
    docs = read_all(CorpusConfig(roots=[first, second]), workers=2)
    assert [(str(d.file_path), d.root_id, d.last_modified) for d in docs] == [
        ("notes.txt", "root_0", (first / "notes.txt").stat().st_mtime), ("b/copy.txt", "root_1", 1_000_000)]
    assert logged() == ["notes.txt"]


# a file that fails in a worker is failed, and the run goes on
@FORK_WARNING
def test_workers_failed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root = tmp_path / "corpus"
    write(root, {"a.txt": NOTES, "b.txt": "Β."})
    def broken(path):
        if path.name == "a.txt":
            raise ValueError("broken")
        return read_text(path)
    monkeypatch.setitem(PARSERS, ".txt", broken)
    assert [(str(d.file_path), d.parse_status) for d in docs_of(root, workers=2)] == [("a.txt", "failed"), ("b.txt", "ok")]


# the workers run at low priority: 10 steps nicer than this process (19 is the most there is)
@FORK_WARNING
def test_workers_run_at_low_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root = tmp_path / "corpus"
    write(root, {"a.txt": "x"})
    monkeypatch.setitem(PARSERS, ".txt", lambda path: str(os.nice(0)))
    mine = os.nice(0)
    [doc] = docs_of(root, workers=1)
    assert doc.raw_text == str(min(mine + 10, 19))
    assert os.nice(0) == mine#this process keeps its priority


# cheap files first and OCR last; each document goes into the cache as soon as it is read, so the cheap ones
# are saved while the OCR still runs (the OCR reader of the test waits to see notes.txt in the cache)
@FORK_WARNING
def test_cheap_files_first_and_saved_before_the_ocr(tmp_path, db, logged, monkeypatch):
    root = tmp_path / "corpus"
    write(root, {"a.pdf": b"%PDF-1.4 fake", "b.png": b"fake png", "notes.txt": NOTES, "z.md": "# Title"})
    path = tmp_path / "data" / "cache.db"
    notes = ScannedFile(root_id="root_0", abs_path=root / "notes.txt", rel_path=Path("notes.txt"), size=0, mtime=0.0,
                        sha256=hashlib.sha256(NOTES.encode("utf-8")).hexdigest())
    def ocr_reader(file, ocr):
        for _ in range(100):#up to 5 seconds
            if get_cached(open_cache(path), notes) is not None:
                return "notes.txt was saved", True
            time.sleep(0.05)
        return "notes.txt was not saved", True
    monkeypatch.setitem(OCR_PARSERS, ".pdf", ocr_reader)
    monkeypatch.setitem(OCR_PARSERS, ".png", ocr_reader)
    docs = docs_of(root, db=db, workers=1)
    assert logged() == ["notes.txt", "z.md"]
    assert [d.raw_text for d in docs if d.ext in (".pdf", ".png")] == ["notes.txt was saved"] * 2


# without workers everything is read in this process, as before (the C2 tests count reads in a list)
def test_without_workers_reads_in_this_process(tmp_path, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    docs_of(root)
    assert reads == ["notes.txt"]


# piece C3b: the files inside archives are read by the workers too (D95)

ARCHIVES = {
    "notes.txt": NOTES,
    "work.zip": zip_bytes({"a/notes.txt": NOTES, "a/other.md": "# Other", "a/main.py": CODE, "a/twice.txt": "Δύο φορές.",
                           "b/twice.txt": "Δύο φορές.", "inner.zip": zip_bytes({"deep.txt": "Βαθιά.", "deep.md": "# Deep"})}),
    "copy.zip": zip_bytes({"x.txt": "Από αντίγραφο."}), "broken.zip": "not a zip",
}


# the documents are the same with workers as without, with archives in archives, copies inside and outside, broken ones
@pytest.mark.filterwarnings("error::DeprecationWarning")
@pytest.mark.parametrize("with_cache", [False, True], ids=["no cache", "cache"])
def test_workers_inside_archives_give_the_same_documents(tmp_path, with_cache):
    root = tmp_path / "corpus"
    write(root, ARCHIVES)
    plain = docs_of(root)
    db = open_cache(tmp_path / "data" / "cache.db") if with_cache else None
    assert docs_of(root, db=db, workers=3) == plain


# the files inside an archive are read in the workers: they run at low priority, this process does not
@FORK_WARNING
def test_files_inside_archives_are_read_in_the_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root = tmp_path / "corpus"
    write(root, {"work.zip": zip_bytes({"a.txt": "x", "inner.zip": zip_bytes({"b.txt": "y"})})})
    monkeypatch.setitem(PARSERS, ".txt", lambda path: str(os.nice(0)))
    mine = os.nice(0)
    docs = docs_of(root, workers=1)
    assert [(str(d.file_path), d.raw_text) for d in docs] == [
        ("work.zip/a.txt", str(min(mine + 10, 19))), ("work.zip/inner.zip/b.txt", str(min(mine + 10, 19)))]


# what is read once stays read once: a file inside that is also outside, and the same file twice inside
@FORK_WARNING
def test_workers_inside_archives_read_each_file_once(tmp_path, logged):
    root = tmp_path / "corpus"
    write(root, ARCHIVES)
    docs_of(root, workers=3)
    assert sorted(logged()) == ["deep.md", "deep.txt", "notes.txt", "other.md", "twice.txt", "x.txt"]


# the second run with the cache reads nothing inside the archives either
@FORK_WARNING
def test_workers_inside_archives_use_the_cache(tmp_path, db, logged):
    root = tmp_path / "corpus"
    write(root, ARCHIVES)
    first = docs_of(root, db=db, workers=2)
    read_once = logged()
    assert docs_of(root, db=db, workers=2) == first
    assert logged() == read_once


# inside an archive too: cheap files first, and each saved as soon as it is read, before the OCR ends
@FORK_WARNING
def test_inside_archives_cheap_files_first_and_saved_before_the_ocr(tmp_path, db, logged, monkeypatch):
    root = tmp_path / "corpus"
    write(root, {"work.zip": zip_bytes({"a.pdf": "%PDF-1.4 fake", "notes.txt": NOTES})})
    path = tmp_path / "data" / "cache.db"
    notes = ScannedFile(root_id="root_0", abs_path=Path("notes.txt"), rel_path=Path("notes.txt"), size=0, mtime=0.0,
                        sha256=hashlib.sha256(NOTES.encode("utf-8")).hexdigest())
    def ocr_reader(file, ocr):
        for _ in range(100):#up to 5 seconds
            if get_cached(open_cache(path), notes) is not None:
                return "notes.txt was saved", True
            time.sleep(0.05)
        return "notes.txt was not saved", True
    monkeypatch.setitem(OCR_PARSERS, ".pdf", ocr_reader)
    docs = docs_of(root, db=db, workers=1)
    assert [(str(d.file_path), d.raw_text) for d in docs] == [("work.zip/a.pdf", "notes.txt was saved"), ("work.zip/notes.txt", NOTES)]


# piece C4: progress while the workers read (D95)


# after each file a worker reads: (done, total)
@FORK_WARNING
def test_progress_of_the_workers(tmp_path, db, logged):
    root = tmp_path / "corpus"
    write(root, {"a.txt": "a", "b.txt": "b", "c.md": "# c", "copy/a.txt": "a"})
    calls = []
    docs_of(root, db=db, workers=2, progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]#the copy is read once
    calls.clear()
    docs_of(root, db=db, workers=2, progress=lambda done, total: calls.append((done, total)))
    assert calls == []#nothing to read the second time


# without workers there is nothing to count
def test_no_progress_without_workers(tmp_path):
    root = tmp_path / "corpus"
    write(root, {"a.txt": "a"})
    calls = []
    docs_of(root, progress=lambda done, total: calls.append((done, total)))
    assert calls == []


# what the scanner ignores inside an archive is counted too, with the path of the archive in front (D99)
def test_read_files_gives_what_was_ignored_inside_archives(tmp_path):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "work.zip": zip_bytes({"a.txt": "Α.", "Thumbs.db": "x", ".git/config": "x"})})
    corpus = CorpusConfig(roots=[root])
    ignored = []
    docs = read_files(scan(corpus).files, corpus, ignored=ignored)
    assert [str(d.file_path) for d in docs] == ["notes.txt", "work.zip/a.txt"]
    assert sorted((str(p), reason) for p, reason in ignored) == [("work.zip/.git", "ignored_dir"), ("work.zip/Thumbs.db", "ignored_file")]
