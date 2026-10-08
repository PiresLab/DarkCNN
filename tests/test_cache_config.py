from pathlib import Path

import pytest
from pydantic import ValidationError

from darkcnn import cache
from darkcnn.config import Config, load_config


def test_key_is_stable_and_sensitive():
    assert cache.key_of(a=1, b="x") == cache.key_of(b="x", a=1)
    assert cache.key_of(a=1) != cache.key_of(a=2)
    assert cache.key_of(p=Path("a/b")) == cache.key_of(p=Path("a/b"))


def test_load_requires_matching_key_and_survives_corruption(tmp_path):
    f = tmp_path / "x.json"
    cache.save(f, "k1", {"v": [1, 2]})
    assert cache.load(f, "k1") == {"v": [1, 2]}
    assert cache.load(f, "k2") is None
    f.write_text("{quebrado")
    assert cache.load(f, "k1") is None
    assert cache.load(tmp_path / "nao_existe.json", "k1") is None


def test_config_defaults_and_validation():
    c = Config()
    assert c.gemini_model and c.min_clip_s == 30 and c.max_clip_s == 60 and c.clips_per_video == 5
    with pytest.raises(ValidationError):
        Config(min_clip_s=60, max_clip_s=30)
    with pytest.raises(ValidationError):
        Config(clips_per_videos=3)  # typo no yaml vira erro, não é ignorado
    with pytest.raises(ValidationError):
        Config(text_mode="legenda")


def test_load_config_yaml_and_overrides(tmp_path):
    y = tmp_path / "config.yaml"
    y.write_text("min_clip_s: 10\nmax_clip_s: 20\nwatermark:\n  opacity: 0.3\n")
    c = load_config(y, {"min_clip_s": None, "clips_per_video": 2, "watermark_path": Path("wm.png")})
    assert c.min_clip_s == 10 and c.clips_per_video == 2
    assert c.watermark.opacity == 0.3 and c.watermark.path == Path("wm.png")


def test_load_config_missing_explicit_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nao.yaml")
