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


# the show command: what the ingest made of a file, and lists by status and by quality (D97)
BAD = chr(0xFFFD)
SHOW_CORPUS = {
    "notes.txt": NOTES, "a/b.md": "# Title\n\nText.", "c/b.md": "# Other", "empty.txt": "", "broken.zip": "not a zip",
    "work.zip": zip_bytes({"in/inside.txt": "Μέσα."}), "bad.txt": "Λέξεις " + BAD + " με χαλασμένο χαρακτήρα.",
}


# a corpus that has been ingested; the show command on it: (stdout, stderr)
@pytest.fixture
def shown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path, SHOW_CORPUS)
    ingest(yaml_file)
    capsys.readouterr()
    def show(*more):
        from rag_fs.cli import main
        main(["show", str(yaml_file), *more])
        return capsys.readouterr().out
    return show


# a file: its path and root, an empty line, status, type and length, then its text as the chunker will get it
# (the format is Giorgos's)
@FORK_WARNING
def test_show_a_file(shown):
    out = shown("notes.txt")
    lines = out.splitlines()
    assert lines[:3] == ["notes.txt    (root_0)", "", f"status:ok    type:prose    len:{len(NOTES)}"]
    assert out.rstrip("\n").endswith("\n\n" + NOTES)


# by its path, by its name only, or inside an archive
@FORK_WARNING
@pytest.mark.parametrize("asked, path", [("a/b.md", "a/b.md"), ("work.zip/in/inside.txt", "work.zip/in/inside.txt"),
                                         ("inside.txt", "work.zip/in/inside.txt")], ids=["path", "inside an archive", "name inside an archive"])
def test_show_finds_the_file(shown, asked, path):
    assert shown(asked).splitlines()[0] == f"{path}    (root_0)"


# a name that two files have: both are shown
@FORK_WARNING
def test_show_same_name_twice(shown):
    headers = [line for line in shown("b.md").splitlines() if line.endswith("    (root_0)")]
    assert headers == ["a/b.md    (root_0)", "c/b.md    (root_0)"]


# the quality reasons of the file are in the header, when it has some
@FORK_WARNING
def test_show_quality_in_the_header(shown):
    assert "quality:replacement_chars" in shown("bad.txt").splitlines()
    assert not any(line.startswith("quality:") for line in shown("notes.txt").splitlines())


# a file that is not in the documents: a message and an error code
@FORK_WARNING
def test_show_unknown_file(shown, capsys):
    with pytest.raises(SystemExit) as stop:
        shown("nothing.txt")
    assert stop.value.code != 0
    assert "nothing.txt" in capsys.readouterr().err


# --status: the paths of the documents with that status, one per line
@FORK_WARNING
@pytest.mark.parametrize("status, paths", [("failed", ["broken.zip"]), ("empty", ["empty.txt"]),
                                           ("ok", ["a/b.md", "bad.txt", "c/b.md", "notes.txt", "work.zip/in/inside.txt"]), ("ocr", [])])
def test_show_status(shown, status, paths):
    assert shown("--status", status).splitlines() == paths


# --quality: the documents with quality reasons, and the reasons
@FORK_WARNING
def test_show_quality(shown):
    assert shown("--quality").splitlines() == ["bad.txt: replacement_chars"]


# exactly one of: a file, --status, --quality
@FORK_WARNING
@pytest.mark.parametrize("more", [[], ["notes.txt", "--quality"], ["--status", "ok", "--quality"], ["notes.txt", "--status", "ok"],
                                  ["--status", "nothing"]], ids=["none", "file and quality", "status and quality", "file and status", "unknown status"])
def test_show_needs_one_question(shown, capsys, more):
    with pytest.raises(SystemExit) as stop:
        shown(*more)
    assert stop.value.code != 0
    assert "invalid choice: 'show'" not in capsys.readouterr().err#the command exists, the question is wrong


# before any ingest there are no documents to show: a message, not a crash
def test_show_before_ingest(tmp_path, capsys):
    from rag_fs.cli import main
    root, data, yaml_file = setup(tmp_path, {"notes.txt": NOTES})
    with pytest.raises(SystemExit) as stop:
        main(["show", str(yaml_file), "notes.txt"])
    assert stop.value.code != 0
    err = capsys.readouterr().err
    assert "invalid choice: 'show'" not in err and "ingest" in err#it says to run the ingest first
