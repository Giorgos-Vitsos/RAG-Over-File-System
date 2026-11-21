import logging

import pytest

from rag_fs.ingest import pipeline


# a new process: the root logger has no handlers yet; pytest adds its own while a test runs,
# so the test empties them itself, and they come back after it
@pytest.fixture
def clean_root(monkeypatch):
    def empty():
        monkeypatch.setattr(logging.root, "handlers", [])
        monkeypatch.setattr(logging.root, "level", logging.WARNING)
    return empty


def show_warnings():
    from rag_fs.logs import show_warnings
    show_warnings()


# a warning first clears the progress line, so it starts on its own line and the next count is drawn again below it
def test_warning_clears_the_progress_line(clean_root, capsys):
    clean_root()
    show_warnings()
    print("\r\033[Kread 46/1859 files", end="", file=__import__("sys").stderr)
    logging.getLogger("rag_fs.ingest.parsers").warning("cannot parse a.txt: not a text file")
    assert capsys.readouterr().err == "\r\033[Kread 46/1859 files\r\033[Kcannot parse a.txt: not a text file\n"


# only the message: no level or logger name in front of it
def test_warning_is_only_the_message(clean_root, capsys):
    clean_root()
    show_warnings()
    logging.getLogger("anything").warning("μήνυμα")
    assert capsys.readouterr().err.endswith("\033[Kμήνυμα\n")


def test_info_is_not_shown(clean_root, capsys):
    clean_root()
    show_warnings()
    logging.getLogger("anything").info("μήνυμα")
    assert capsys.readouterr().err == ""


# the files are read in other processes (forkserver), which start without the settings of the main one
def test_workers_show_warnings_the_same_way(clean_root, capsys, monkeypatch):
    clean_root()
    monkeypatch.setattr(pipeline.os, "nice", lambda value: 0)
    pipeline.start_new_process()
    logging.getLogger("rag_fs.ingest.parsers").warning("cannot parse a.txt: not a text file")
    assert capsys.readouterr().err == "\r\033[Kcannot parse a.txt: not a text file\n"


# the command line sets it up before anything is read
def test_cli_shows_warnings_the_same_way(clean_root, capsys):
    clean_root()
    from rag_fs.cli import main
    with pytest.raises(SystemExit):
        main(["show"])
    capsys.readouterr()
    logging.getLogger("rag_fs.ingest.parsers").warning("w")
    assert capsys.readouterr().err == "\r\033[Kw\n"
