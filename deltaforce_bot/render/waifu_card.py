"""猪结婚拼图：把两个头像裁切填进模板的左右白色占位。

模板 ``assets/memes/waifu/猪结婚.jpg`` 为 1012×1024；
白色占位坐标来自人工测量，换模板时需要同步修改 ``SENDER_BOX`` / ``TARGET_BOX``。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

try:  # 缺少 Pillow 时让功能降级，不影响其他插件启动
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - 部署会安装依赖，这里只防御跳过依赖的情况
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

TEMPLATE_SIZE = (1012, 1024)
SENDER_BOX = (239, 309, 504, 627)  # 发送者头像；265×318
TARGET_BOX = (534, 346, 817, 623)  # 被选中群友头像；283×277
DEFAULT_QUALITY = 92


class WaifuRenderError(RuntimeError):
    """模板/头像解码/拼图失败。"""


def _require_pillow() -> None:
    if Image is None or ImageOps is None:
        raise WaifuRenderError("缺少 Pillow，请安装 pillow 依赖")


def load_template(path: Path | str) -> "Image.Image":
    _require_pillow()
    template_path = Path(path)
    try:
        with Image.open(template_path) as image:
            template = image.convert("RGB")
    except (OSError, ValueError) as exc:
        raise WaifuRenderError(f"读取猪结婚模板失败：{template_path}：{exc}") from exc
    if template.size != TEMPLATE_SIZE:
        raise WaifuRenderError(
            f"猪结婚模板尺寸应为 {TEMPLATE_SIZE}，实际为 {template.size}；"
            "换模板后需要同步修改头像坐标"
        )
    return template


def decode_avatar(data: bytes) -> "Image.Image":
    _require_pillow()
    if not data:
        raise WaifuRenderError("头像为空")
    try:
        with Image.open(io.BytesIO(data)) as image:
            # 手机上传的图可能带 EXIF 旋转；统一转成 RGB
            return ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError) as exc:
        raise WaifuRenderError(f"头像解码失败：{exc}") from exc


def compose(
    template: "Image.Image",
    sender_avatar: "Image.Image",
    target_avatar: "Image.Image",
    *,
    quality: int = DEFAULT_QUALITY,
) -> bytes:
    """把两个头像裁切填满左右白块，返回 JPEG bytes。"""
    _require_pillow()
    output = template.copy()
    for box, avatar in ((SENDER_BOX, sender_avatar), (TARGET_BOX, target_avatar)):
        x0, y0, x1, y1 = box
        fitted = ImageOps.fit(
            avatar,
            (x1 - x0, y1 - y0),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        output.paste(fitted, (x0, y0))

    buffer = io.BytesIO()
    try:
        output.save(buffer, format="JPEG", quality=quality, optimize=True)
    except (OSError, ValueError) as exc:
        raise WaifuRenderError(f"猪结婚拼图保存失败：{exc}") from exc
    return buffer.getvalue()
