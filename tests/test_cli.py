from pathlib import Path

import pytest

from rag_fs.ingest import pipeline
from rag_fs.ingest.cache import get_cached, open_cache
from rag_fs.ingest.file_scanner import scan
import json

from rag_fs.config import CorpusConfig, load_config
from rag_fs.ingest.chunker import chunk_doc, length_stats
from rag_fs.models import Chunk, Document, load_jsonl, save_jsonl
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


# the summary counts what was ignored inside archives too, still without names (D99)
@FORK_WARNING
def test_ingest_summary_counts_ignored_inside_archives(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path, {"notes.txt": NOTES, "Thumbs.db": "x",
                                             "work.zip": zip_bytes({"a.txt": "Α.", "desktop.ini": "x", ".git/config": "x"})})
    ingest(yaml_file)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "Ignored 3" in lines and "ignored_file 2" in lines and "ignored_dir 1" in lines
    assert "desktop" not in out and ".git" not in out


# the progress line is cleared before it is written again, so a shorter line leaves nothing of a longer one
@FORK_WARNING
def test_progress_line_is_cleared(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "START_METHOD", "fork")
    root, data, yaml_file = setup(tmp_path, {"a.txt": "a", "b.txt": "b"})
    ingest(yaml_file)
    err = capsys.readouterr().err
    assert "\r\x1b[Kread 2/2 files" in err


# K6b: the chunk command (one file of chunks and one of stats per strategy), --sample, show --chunks (D113)

def parsed(path, text, content_type="prose", status="ok"):
    return Document(file_path=Path(path), root_id="root_0", ext=Path(path).suffix, raw_text=text, last_modified=0.0,
                    content_type=content_type, parse_status=status, sha256="0" * 64)


CHUNK_DOCS = [
    parsed("secret-plans/notes.md", "# Α\n\nΠρώτο κείμενο.\n\n## Β\n\nΔεύτερο κείμενο εδώ."),
    parsed("secret-plans/grades.csv", "Όνομα: Άννα; Βαθμός: 9\nΌνομα: Νίκος; Βαθμός: 7", "tabular"),
    parsed("secret-plans/main.py", "def f():\n    return 1\n\ndef g():\n    return 2", "code"),
    parsed("secret-plans/old.zip", "", "binary", "failed"),
    parsed("secret-plans/empty.txt", "", "prose", "empty"),
]


# documents saved as the ingest would save them, and a yaml that chunks them into out/chunks
def chunk_setup(tmp_path, docs=CHUNK_DOCS, more=""):
    documents = tmp_path / "docs" / "documents.jsonl"
    documents.parent.mkdir(parents=True)
    save_jsonl(docs, documents)
    yaml_file = tmp_path / "exp.yaml"
    yaml_file.write_text(f"ingest:\n  documents: \"{documents.as_posix()}\"\n"
                         f"chunking:\n  chunks_dir: \"{(tmp_path / 'out' / 'chunks').as_posix()}\"\n{more}",
                         encoding="utf-8")
    return yaml_file, tmp_path / "out" / "chunks"


def run_chunk(yaml_file, *more):
    from rag_fs.cli import main
    main(["chunk", str(yaml_file), *more])


def chunked(docs, config):
    return [c for doc in docs for c in chunk_doc(doc, config.chunking)]


# the chunks of every document, in order, as chunk_doc gives them with the settings of the yaml
@pytest.mark.parametrize("more", ["", "  narrative_words: 3\n  tabular_rows: 1\n"], ids=["defaults", "settings"])
def test_chunk_saves_the_chunks(tmp_path, more):
    yaml_file, folder = chunk_setup(tmp_path, more=more)
    run_chunk(yaml_file)
    assert load_jsonl(Chunk, folder / "chunks-content_aware.jsonl") == chunked(CHUNK_DOCS, load_config(yaml_file))


# every strategy has its own file, so the naive chunks never overwrite the content-aware ones
def test_chunk_one_file_per_strategy(tmp_path):
    yaml_file, folder = chunk_setup(tmp_path)
    run_chunk(yaml_file)
    naive_yaml = tmp_path / "naive.yaml"
    naive_yaml.write_text(yaml_file.read_text(encoding="utf-8") + "  strategy: naive\n", encoding="utf-8")
    run_chunk(naive_yaml)
    assert load_jsonl(Chunk, folder / "chunks-content_aware.jsonl") == chunked(CHUNK_DOCS, load_config(yaml_file))
    naive = load_jsonl(Chunk, folder / "chunks-naive.jsonl")
    assert naive == chunked(CHUNK_DOCS, load_config(naive_yaml))
    assert {c.chunk_strategy for c in naive} == {"naive"}


# next to the chunks: the strategy, its settings, the number of documents, and the length table of all
# the chunks and of each content type
def test_chunk_saves_the_stats(tmp_path):
    yaml_file, folder = chunk_setup(tmp_path, more="  narrative_words: 3\n")
    run_chunk(yaml_file)
    chunks = chunked(CHUNK_DOCS, load_config(yaml_file))
    stats = json.loads((folder / "chunks-content_aware.stats.json").read_text(encoding="utf-8"))
    assert stats == {
        "strategy": "content_aware",
        "settings": {"narrative_words": 3, "overlap": 0.2, "tabular_rows": 20},
        "documents": 5,
        "all": length_stats(chunks),
        "by_type": {kind: length_stats([c for c in chunks if c.content_type == kind])
                    for kind in ["binary", "code", "prose", "tabular"]},
    }


# what it prints: how many chunks from how many documents, then one line per type (in order) and one for all:
# chunks, empty, words mean/median/p90, chars mean/median/p90, with the mean and the median to one decimal
def test_chunk_summary(tmp_path, capsys):
    yaml_file, folder = chunk_setup(tmp_path)
    run_chunk(yaml_file)
    chunks = chunked(CHUNK_DOCS, load_config(yaml_file))

    def line(name, found):
        s = length_stats(found)
        w, c = s["words"], s["chars"]
        return (f"{name}: {s['chunks']} chunks, {s['empty']} empty, words {w['mean']:.1f}/{w['median']:.1f}/{w['p90']}, "
                f"chars {c['mean']:.1f}/{c['median']:.1f}/{c['p90']}")
    assert capsys.readouterr().out.splitlines() == [
        f"Chunks {len(chunks)} from 5 documents (content_aware)", "",
        *[line(kind, [c for c in chunks if c.content_type == kind]) for kind in ["binary", "code", "prose", "tabular"]],
        line("all", chunks)]


# the summary is safe to share: numbers only, no names of files
def test_chunk_summary_has_no_file_names(tmp_path, capsys):
    yaml_file, folder = chunk_setup(tmp_path)
    run_chunk(yaml_file)
    out = capsys.readouterr().out
    assert "Chunks" in out and "secret" not in out and "Άννα" not in out


# the folder of the chunks is made if it is missing
def test_chunk_makes_the_folder(tmp_path):
    yaml_file, folder = chunk_setup(tmp_path)
    assert not folder.exists()
    run_chunk(yaml_file)
    assert (folder / "chunks-content_aware.jsonl").is_file() and (folder / "chunks-content_aware.stats.json").is_file()


# before any ingest there are no documents to chunk: a message that says to run the ingest, not a crash
def test_chunk_before_ingest(tmp_path, capsys):
    yaml_file, folder = chunk_setup(tmp_path)
    (tmp_path / "docs" / "documents.jsonl").unlink()
    with pytest.raises(SystemExit) as stop:
        run_chunk(yaml_file)
    assert stop.value.code != 0
    err = capsys.readouterr().err
    assert "invalid choice: 'chunk'" not in err and "ingest" in err
    assert not folder.exists()


# a corpus with many chunks of each type: 25 sections, 20 rows (one per chunk), 2 functions
SAMPLE_DOCS = [
    parsed("a/notes.md", "\n\n".join(f"# Ενότητα {i}\n\nΚείμενο {i}." for i in range(25))),
    parsed("a/table.csv", "\n".join(f"Όνομα: φοιτητής{i}; Βαθμός: {i % 10}" for i in range(20)), "tabular"),
    parsed("a/main.py", "def f():\n    return 1\n\ndef g():\n    return 2", "code"),
    parsed("a/old.zip", "", "binary", "failed"),
    parsed("a/empty.txt", "", "prose", "empty"),
]
SAMPLE_SETTINGS = "  tabular_rows: 1\n  narrative_words: 4\n"


def sampled(tmp_path, capsys, n, seed=14):
    yaml_file, folder = chunk_setup(tmp_path, SAMPLE_DOCS, SAMPLE_SETTINGS + f"seed: {seed}\n")
    run_chunk(yaml_file, "--sample", str(n))
    out = capsys.readouterr().out
    chunks = {c.chunk_id: c for c in chunked(SAMPLE_DOCS, load_config(yaml_file))}
    return out, chunks


# the sample, after the summary: for each type in order, "=== type: k of n ===" (n = its chunks with text),
# then each chunk as "--- chunk_id", its index_text, and an empty line
def blocks(out):
    found = {}
    kind = None
    lines = out.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("=== "):
            kind = line[4:line.index(":")]
            found[kind] = {"header": line, "ids": []}
        elif line.startswith("--- "):
            found[kind]["ids"].append(line[4:])
    return found


def test_chunk_sample(tmp_path, capsys):
    out, chunks = sampled(tmp_path, capsys, 3)
    found = blocks(out)
    with_text = {kind: [c for c in chunks.values() if c.content_type == kind and c.text] for kind in ["code", "prose", "tabular"]}
    assert list(found) == ["code", "prose", "tabular"]  # in order; binary has only an empty chunk, so it has no sample
    for kind, picked in found.items():
        k = min(3, len(with_text[kind]))
        assert picked["header"] == f"=== {kind}: {k} of {len(with_text[kind])} ==="
        assert len(picked["ids"]) == len(set(picked["ids"])) == k
        for chunk_id in picked["ids"]:
            c = chunks[chunk_id]
            assert c.content_type == kind and c.text  # never an empty chunk
            assert f"--- {chunk_id}\n{c.index_text}\n\n" in out
    assert out.index("Chunks ") < out.index("=== ")  # the summary comes first


# a type with fewer chunks than asked shows all of them
def test_chunk_sample_bigger_than_a_type(tmp_path, capsys):
    out, chunks = sampled(tmp_path, capsys, 50)
    found = blocks(out)
    assert list(found) == ["code", "prose", "tabular"]  # without this, an empty output would pass
    for kind, picked in found.items():
        assert sorted(picked["ids"]) == sorted(c.chunk_id for c in chunks.values() if c.content_type == kind and c.text)


# the seed of the config makes the sample the same every time, and another seed gives another sample
def test_chunk_sample_follows_the_seed(tmp_path, capsys):
    first, _ = sampled(tmp_path / "one", capsys, 3)
    again, _ = sampled(tmp_path / "two", capsys, 3)
    other, _ = sampled(tmp_path / "three", capsys, 3, seed=15)
    assert first == again
    assert blocks(first)["prose"]["ids"] != blocks(other)["prose"]["ids"]


# a sample of 0 or fewer chunks is a mistake in the command
@pytest.mark.parametrize("n", ["0", "-2"])
def test_chunk_sample_must_be_positive(tmp_path, capsys, n):
    yaml_file, folder = chunk_setup(tmp_path)
    with pytest.raises(SystemExit) as stop:
        run_chunk(yaml_file, "--sample", n)
    assert stop.value.code != 0
    assert "invalid choice: 'chunk'" not in capsys.readouterr().err


def show_chunks(yaml_file, capsys, *args):
    from rag_fs.cli import main
    main(["show", str(yaml_file), *args])
    return capsys.readouterr().out


# show FILE --chunks: the path and root, an empty line, "chunks N (strategy)", an empty line, then each chunk as
# "--- chunk_id", its index_text and an empty line; the chunks are made now, with the settings of the yaml
@pytest.mark.parametrize("more", ["", "  strategy: naive\n  narrative_words: 3\n"], ids=["content_aware", "naive"])
def test_show_chunks(tmp_path, capsys, more):
    yaml_file, folder = chunk_setup(tmp_path, more=more)
    config = load_config(yaml_file)
    out = show_chunks(yaml_file, capsys, "secret-plans/notes.md", "--chunks")
    expected = chunk_doc(CHUNK_DOCS[0], config.chunking)
    assert out == (f"secret-plans/notes.md    (root_0)\n\nchunks {len(expected)} ({config.chunking.strategy})\n\n"
                   + "".join(f"--- {c.chunk_id}\n{c.index_text}\n\n" for c in expected))
    assert not folder.exists()  # it does not need the chunk command, and writes nothing


# a file without text shows its one chunk with only the header; a name that two files have shows both
def test_show_chunks_empty_and_by_name(tmp_path, capsys):
    docs = CHUNK_DOCS + [parsed("other/notes.md", "Άλλο.")]
    yaml_file, folder = chunk_setup(tmp_path, docs)
    out = show_chunks(yaml_file, capsys, "old.zip", "--chunks")
    assert out == "secret-plans/old.zip    (root_0)\n\nchunks 1 (content_aware)\n\n--- secret-plans/old.zip#0-0\nFile: secret-plans/old.zip\n\n\n\n"
    headers = [line for line in show_chunks(yaml_file, capsys, "notes.md", "--chunks").splitlines()
               if line.endswith("    (root_0)")]
    assert headers == ["secret-plans/notes.md    (root_0)", "other/notes.md    (root_0)"]


# --chunks goes with a file only
@pytest.mark.parametrize("more", [["--chunks"], ["--status", "ok", "--chunks"], ["--quality", "--chunks"]],
                         ids=["alone", "with status", "with quality"])
def test_show_chunks_needs_a_file(tmp_path, capsys, more):
    yaml_file, folder = chunk_setup(tmp_path)
    with pytest.raises(SystemExit) as stop:
        show_chunks(yaml_file, capsys, *more)
    assert stop.value.code != 0
    assert "unrecognized arguments" not in capsys.readouterr().err  # the option exists, it is used wrong


# without --chunks, show prints the text as before
def test_show_without_chunks_is_unchanged(tmp_path, capsys):
    yaml_file, folder = chunk_setup(tmp_path)
    out = show_chunks(yaml_file, capsys, "secret-plans/notes.md")
    assert out.splitlines()[:3] == ["secret-plans/notes.md    (root_0)", "", f"status:ok    type:prose    len:{len(CHUNK_DOCS[0].raw_text)}"]
    assert "--- " not in out


# K8: inside an archive the line says which one
def test_progress_line_names_the_archive(capsys):
    from rag_fs.cli import show_progress
    show_progress(3, 40, "notes/old.zip")
    show_progress(5, 9, "")
    assert capsys.readouterr().err == "\r\x1b[Kread 3/40 files in notes/old.zip\r\x1b[Kread 5/9 files"
