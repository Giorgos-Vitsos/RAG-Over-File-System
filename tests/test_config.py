from pathlib import Path

import pytest

from rag_fs.config import ChunkingConfig, Config, load_config


def test_defaults_need_no_yaml():
    config = Config()
    assert config.seed == 14
    assert config.chunking.strategy == "content_aware"


def test_empty_yaml_gives_all_defaults(tmp_path):
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")

    assert load_config(yaml_file) == Config()


def test_example_yaml_is_valid():
    config = load_config("configs/example.yaml")
    assert isinstance(config, Config)


def test_override_changes_only_what_it_mentions(tmp_path):
    yaml_file = tmp_path / "exp.yaml"
    yaml_file.write_text("retrieval:\n  top_k: 10\n")

    config = load_config(yaml_file)

    assert config.retrieval.top_k == 10
    assert config.retrieval.rrf_k == Config().retrieval.rrf_k
    assert config.chunking == ChunkingConfig()


def test_roots_are_converted_to_path(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("corpus:\n  roots: [\"~/Documents\"]\n")

    config = load_config(yaml_file)

    assert len(config.corpus.roots) == 1
    assert isinstance(config.corpus.roots[0], Path)


def test_yaml_is_read_as_utf8(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("corpus:\n  roots: [\"/data/Έγγραφα\"]\n", encoding="utf-8")

    config = load_config(yaml_file)

    assert config.corpus.roots == [Path("/data/Έγγραφα")]


def test_missing_group_falls_back_to_defaults(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("seed: 99\n")

    config = load_config(yaml_file)

    assert config.seed == 99
    assert config.chunking.strategy == "content_aware"


def test_unknown_key_raises_error(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("retrieval:\n  top_kk: 5\n")

    with pytest.raises(TypeError):
        load_config(yaml_file)


def test_unknown_group_raises_error(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("chucking:\n  overlap: 0.4\n")

    with pytest.raises(ValueError):
        load_config(yaml_file)
