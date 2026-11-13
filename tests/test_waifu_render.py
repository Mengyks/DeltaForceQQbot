"""猪结婚拼图：头像裁切填满左右白块、模板校验。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from deltaforce_bot.render.waifu_card import (
    SENDER_BOX,
    TARGET_BOX,
    TEMPLATE_SIZE,
    WaifuRenderError,
    compose,
    decode_avatar,
    load_template,
)


def make_jpeg(color: tuple[int, int, int], size: tuple[int, int] = (640, 640)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_compose_fills_both_boxes_with_avatar_colors() -> None:
    template = Image.new("RGB", TEMPLATE_SIZE, (255, 255, 255))
    sender = decode_avatar(make_jpeg((220, 30, 30)))
    target = decode_avatar(make_jpeg((30, 30, 220)))

    data = compose(template, sender, target)

    result = Image.open(io.BytesIO(data)).convert("RGB")
    assert result.size == TEMPLATE_SIZE

    def center(box):
        x0, y0, x1, y1 = box
        return result.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))

    left = center(SENDER_BOX)
    right = center(TARGET_BOX)
    assert left[0] > 180 and left[2] < 80
    assert right[2] > 180 and right[0] < 80


def test_compose_does_not_mutate_template() -> None:
    template = Image.new("RGB", TEMPLATE_SIZE, (255, 255, 255))
    sender = decode_avatar(make_jpeg((220, 30, 30)))
    target = decode_avatar(make_jpeg((30, 30, 220)))

    compose(template, sender, target)

    x0, y0, x1, y1 = SENDER_BOX
    assert template.getpixel(((x0 + x1) // 2, (y0 + y1) // 2)) == (255, 255, 255)


def test_load_template_rejects_wrong_size(tmp_path: Path) -> None:
    path = tmp_path / "template.jpg"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(path, format="JPEG")

    with pytest.raises(WaifuRenderError, match="尺寸"):
        load_template(path)


def test_load_template_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WaifuRenderError, match="读取"):
        load_template(tmp_path / "missing.jpg")


def test_decode_avatar_rejects_garbage() -> None:
    with pytest.raises(WaifuRenderError):
        decode_avatar(b"not an image")
