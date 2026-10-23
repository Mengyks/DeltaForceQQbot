"""本地表情包索引测试（临时目录，不依赖真实图库）。"""

from __future__ import annotations

import json

from deltaforce_bot.adapters.memes import MemeLibrary, clean_name

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20


def make_pack(tmp_path, *, max_bytes=1024 * 1024) -> MemeLibrary:
    memes = tmp_path / "memes"
    (memes / "happy").mkdir(parents=True)
    (memes / "_kg").mkdir(parents=True)
    (memes / "happy" / "a.png").write_bytes(PNG)
    (memes / "happy" / "b.gif").write_bytes(b"GIF89a" + b"x" * 20)
    (memes / "_kg" / "c.jpg").write_bytes(b"\xff\xd8" + b"x" * 20)
    manifest = {
        "categories": {
            "happy": {"description": "开心/庆祝"},
            "\u200c_kg": {"description": "库狗调侃"},
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return MemeLibrary(tmp_path, max_bytes=max_bytes)


def test_indexes_categories_and_cleans_zero_width(tmp_path) -> None:
    library = make_pack(tmp_path)

    assert library.available
    assert [c.name for c in library.categories] == ["_kg", "happy"]
    assert library.categories[0].description == "库狗调侃"
    assert library.categories[1].description == "开心/庆祝"
    assert clean_name("\u200c_kg") == "_kg"


def test_tool_spec_contains_category_enum(tmp_path) -> None:
    library = make_pack(tmp_path)
    spec = library.tool_spec()

    assert spec["function"]["name"] == "send_meme"
    enum = spec["function"]["parameters"]["properties"]["category"]["enum"]
    assert set(enum) == {"_kg", "happy"}


def test_pick_returns_image_and_honours_avoid(tmp_path) -> None:
    library = make_pack(tmp_path)
    first = library.pick("happy")
    assert first is not None
    assert first.data.startswith((PNG[:8], b"GIF89a"))

    second = library.pick("happy", avoid={str(first.path)})
    assert second is not None
    assert second.path != first.path

    # 只有一个候选时，即使用 avoid 也会兜底返回
    only = library.pick("_kg")
    assert only is not None
    again = library.pick("_kg", avoid={str(only.path)})
    assert again is not None


def test_missing_category_and_size_limit(tmp_path) -> None:
    large = tmp_path / "large"
    (large / "memes" / "happy").mkdir(parents=True)
    (large / "memes" / "happy" / "big.png").write_bytes(b"x" * 2048)
    (large / "manifest.json").write_text(
        json.dumps({"categories": {"happy": {"description": "大图"}}}),
        encoding="utf-8",
    )
    library = MemeLibrary(large, max_bytes=1024)
    assert not library.available
    assert library.pick("happy") is None

    library2 = make_pack(tmp_path / "second")
    assert library2.pick("missing") is None
