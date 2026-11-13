"""每日天尊挑战的结果模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyChallengeResult:
    monster_name: str
    weapon_name: str
    image: bytes
