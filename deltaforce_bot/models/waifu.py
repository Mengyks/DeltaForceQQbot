"""猪结婚的数据模型：群成员候选与当天配对结果。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WaifuMember:
    """群成员候选；name 是群名片，没有则 QQ 昵称/QQ 号。"""

    user_id: int
    name: str
    is_robot: bool = False


@dataclass(frozen=True)
class WaifuResult:
    """一次查询的结果：伴侣 + 拼好的图片。"""

    partner_id: int
    partner_name: str
    image: bytes
