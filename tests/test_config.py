from pathlib import Path

from rag_fs.config import load_config


def test_default_config_loads():
    config = load_config("configs/default.yaml")
    assert config.seed == 14
    assert config.chunking.strategy == "content_aware"


def test_roots_are_converted_to_path(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("corpus:\n  roots: [\"~/Documents\"]\n")

    config = load_config(yaml_file)

    assert len(config.corpus.roots) == 1
    assert isinstance(config.corpus.roots[0], Path)


def test_missing_group_falls_back_to_defaults(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("seed: 99\n") 

    config = load_config(yaml_file)

    assert config.seed == 99
    assert config.chunking.strategy == "content_aware" 


def test_unknown_key_raises_error(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("retrieval:\n  top_kk: 5\n") 

    try:
        load_config(yaml_file)
        assert False, "expected a TypeError, but none was raised"
    except TypeError:
        pass