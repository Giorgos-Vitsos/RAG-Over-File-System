from pathlib import Path

import pytest

from rag_fs.ingest import pipeline
from rag_fs.ingest.cache import get_cached, open_cache
from rag_fs.ingest.file_scanner import scan
from rag_fs.config import CorpusConfig
from rag_fs.models import Document, load_jsonl
from test_pipeline import CODE, NOTES, logged, read_all, write, zip_bytes  # noqa: F401 (logged is a fixture)

FORK_WARNING = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")

CORPUS = {
    "notes.txt": NOTES, "b.md": "# Title", "main.py": CODE, "empty.txt": "", "broken.zip": "not a zip",
    "image.class": b"\xca\xfe\xba\xbe", "work.zip": zip_bytes({"in/inside.txt": "Μέσα."}),
    ".git/config": "[core]", "Thumbs.db": b"\x00\x01",
}


# a corpus and a yaml that reads it with one worker; the cache goes to out/data, the documents to out/docs
def setup(tmp_path, files=CORPUS, workers=1):
    root = tmp_path / "corpus"
    write(root, files)
    data = tmp_path / "out" / "data"
    yaml_file = tmp_path / "exp.yaml"
    yaml_file.write_text(f"corpus:\n  roots: [\"{root.as_posix()}\"]\n"
                         f"ingest:\n  workers: {workers}\n  cache: \"{(data / 'cache.db').as_posix()}\"\n"
                         f"  documents: \"{(data.parent / 'docs' / 'documents.jsonl').as_posix()}\"\n", encoding="utf-8")
    return root, data, yaml_file


def ingest(yaml_file, *more):
    from rag_fs.cli import main
    main(["ingest", str(yaml_file), *more])


# the documents of the corpus are saved as JSONL where the yaml says, the same as read_files gives
@FORK_WARNING
def test_ingest_saves_the_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    assert load_jsonl(Document, data.parent / "docs" / "documents.jsonl") == read_all(CorpusConfig(roots=[root]))


# the cache is where the yaml says, and has what was read
@FORK_WARNING
def test_ingest_keeps_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    notes = next(f for f in scan(CorpusConfig(roots=[root])).files if f.rel_path.name == "notes.txt")
    found = get_cached(open_cache(data / "cache.db"), notes)
    assert found is not None and found.raw_text == NOTES


# the second ingest reads nothing
@FORK_WARNING
def test_ingest_twice_reads_once(tmp_path, logged):
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    first = logged()
    ingest(yaml_file)
    assert sorted(first) == ["b.md", "empty.txt", "inside.txt", "notes.txt"]
    assert logged() == first


# --rebuild with extensions reads those again, written in any way: .txt, txt, TXT
@FORK_WARNING
@pytest.mark.parametrize("ext", [".txt", "txt", "TXT", ".Txt"])
def test_ingest_rebuild_some_extensions(tmp_path, logged, ext):
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    first = len(logged())
    ingest(yaml_file, "--rebuild", ext)
    assert sorted(logged()[first:]) == ["empty.txt", "inside.txt", "notes.txt"]


# --rebuild with two extensions, and --rebuild alone reads everything again
@FORK_WARNING
@pytest.mark.parametrize("more, again", [(["--rebuild", "txt", ".md"], ["b.md", "empty.txt", "inside.txt", "notes.txt"]),
                                         (["--rebuild"], ["b.md", "empty.txt", "inside.txt", "notes.txt"])],
                         ids=["two extensions", "everything"])
def test_ingest_rebuild(tmp_path, logged, more, again):
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    first = len(logged())
    ingest(yaml_file, *more)
    assert sorted(logged()[first:]) == again


# the summary: documents by status, by extension, and what the scanner ignored and why (only numbers)
@FORK_WARNING
def test_ingest_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path)
    ingest(yaml_file)
    lines = capsys.readouterr().out.splitlines()
    for line in ["Documents 7", "ok 4", "empty 1", "failed 1", "unsupported 1",
                 ".txt ok 2, empty 1", ".md ok 1", ".py ok 1", ".zip failed 1", ".class unsupported 1",
                 "Ignored 2", "ignored_dir 1", "ignored_file 1"]:
        assert line in lines
    assert "ocr 0" not in lines#statuses with no documents are not shown


# the summary has no names of files: it is safe to share
@FORK_WARNING
def test_ingest_summary_has_no_file_names(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path, {"secret-plans.txt": NOTES, "private.zip": "not a zip",
                                             "hidden-photos/Thumbs.db": b"\x00", "hidden-git/.git/config": "x"})
    ingest(yaml_file)
    out = capsys.readouterr().out
    assert "secret" not in out and "private" not in out and "hidden" not in out and str(root) not in out
    assert "Ignored 2" in out.splitlines()#the ignored files are counted, not named


# while it reads, it shows how many files of how many are done (on stderr, so the summary stays clean)
@FORK_WARNING
def test_ingest_shows_progress(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})
    ingest(yaml_file)
    err = capsys.readouterr().err
    assert "3/3" in err


# the folders of the outputs are made if they are missing
@FORK_WARNING
def test_ingest_makes_the_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path)
    assert not data.parent.exists()
    ingest(yaml_file)
    assert (data.parent / "docs" / "documents.jsonl").is_file() and (data / "cache.db").is_file()


# a yaml without roots has nothing to read: it stops with a message instead of reading nothing
def test_ingest_without_roots(tmp_path):
    yaml_file = tmp_path / "exp.yaml"
    yaml_file.write_text("seed: 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        ingest(yaml_file)
    assert stop.value.code != 0


# the scanner report is still there, as "scan"
def test_scan_command(tmp_path, capsys):
    from rag_fs.cli import main
    root = tmp_path / "corpus"
    write(root, {"a.txt": "a", "b.py": "b", ".git/config": "x"})
    main(["scan", str(root)])
    out = capsys.readouterr().out
    assert "Found 2 files" in out and "ignored_dir \t 1" in out


# a command that does not exist, or no command, stops with the usage
@pytest.mark.parametrize("argv", [["nothing"], []], ids=["unknown", "none"])
def test_cli_without_a_known_command(argv):
    from rag_fs.cli import main
    with pytest.raises(SystemExit) as stop:
        main(argv)
    assert stop.value.code != 0


# piece C5: the ingest hashes again only the files that changed (D95)


@pytest.fixture
def hashed(monkeypatch):
    from rag_fs.ingest import file_scanner
    names = []
    real = file_scanner.file_sha256
    def counted(path):
        names.append(Path(path).name)
        return real(path)
    monkeypatch.setattr(file_scanner, "file_sha256", counted)
    return names


# the second ingest opens no file of the corpus; a file that changed is hashed and read again
@FORK_WARNING
def test_ingest_hashes_only_what_changed(tmp_path, logged, hashed):
    root, data, yaml_file = setup(tmp_path, {"notes.txt": NOTES, "b.md": "# Title", "work.zip": zip_bytes({"in.txt": "Μέσα."})})
    ingest(yaml_file)
    hashed.clear()
    reads = len(logged())
    ingest(yaml_file)
    assert [name for name in hashed if name != "in.txt"] == []#the files inside an archive are in a new temp folder every time
    (root / "notes.txt").write_text("Άλλες σημειώσεις.", encoding="utf-8")
    hashed.clear()
    ingest(yaml_file)
    assert [name for name in hashed if name != "in.txt"] == ["notes.txt"]
    assert logged()[reads:] == ["notes.txt"]
    docs = {str(d.file_path): d.raw_text for d in load_jsonl(Document, data.parent / "docs" / "documents.jsonl")}
    assert docs["notes.txt"] == "Άλλες σημειώσεις."


# a new file is hashed, and a file that is gone is not in the documents
@FORK_WARNING
def test_ingest_new_and_removed_files(tmp_path, logged, hashed):
    root, data, yaml_file = setup(tmp_path, {"notes.txt": NOTES, "old.txt": "Παλιό."})
    ingest(yaml_file)
    (root / "old.txt").unlink()
    (root / "new.txt").write_text("Νέο.", encoding="utf-8")
    hashed.clear()
    ingest(yaml_file)
    assert hashed == ["new.txt"]
    docs = [str(d.file_path) for d in load_jsonl(Document, data.parent / "docs" / "documents.jsonl")]
    assert docs == ["new.txt", "notes.txt"]
