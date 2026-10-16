"""卡战备入口（地图 + 难度 → 接口档位）的模型与解析。

数据全部来自 config/gates.json，改地图/难度/别名不需要改代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from deltaforce_bot.adapters.errors import DataSourceError


class Gate(BaseModel):
    """一个可查询的卡战备入口：某个地图的某个难度。"""

    model_config = ConfigDict(extra="ignore")

    map: str
    difficulty: str
    lv: int
    threshold: int
    map_aliases: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.map} {self.difficulty}"

    def matches_map(self, text: str) -> bool:
        needle = _norm(text)
        return needle in {_norm(self.map), *(_norm(a) for a in self.map_aliases)}


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    difficulties: list[str] = ["常规", "机密", "绝密"]
    regular_note: str = "常规模式不需要战备值，不提供卡战备建议"
    gates: list[Gate] = Field(default_factory=list)

    def maps(self) -> list[str]:
        seen: list[str] = []
        for gate in self.gates:
            if gate.map not in seen:
                seen.append(gate.map)
        return seen

    def gates_of(self, map_name: str) -> list[Gate]:
        return [gate for gate in self.gates if gate.map == map_name]

    def find(self, map_name: str, difficulty: str) -> Gate | None:
        for gate in self.gates:
            if gate.map == map_name and gate.difficulty == difficulty:
                return gate
        return None


@dataclass(frozen=True)
class GateResolution:
    """解析结果：要么给出入口，要么给出面向用户的错误提示。"""

    gate: Gate | None = None
    error: str | None = None
    suggestions: tuple[Gate, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.gate is not None


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def load_gate_config(path: Path) -> GateConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataSourceError(f"卡战备入口配置不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"卡战备入口配置不是合法 JSON：{path}（{exc}）") from exc

    config = GateConfig.model_validate(raw)
    if not config.gates:
        raise DataSourceError(f"卡战备入口配置里没有任何档位：{path}")
    return config


def resolve_gate(
    config: GateConfig,
    map_text: str | None = None,
    difficulty_text: str | None = None,
) -> GateResolution:
    """按「地图 难度」解析入口，错误信息尽量具体、可操作。"""
    if not map_text:
        return GateResolution(error="请给出地图和难度，例如：卡战备 巴克什 绝密（发送「卡战备」看全部档位）")

    gate_map = _find_map(config, map_text)
    if gate_map is None:
        maps = "、".join(config.maps())
        return GateResolution(error=f"不认识地图「{map_text}」。可用地图：{maps}")

    candidates = config.gates_of(gate_map)

    if difficulty_text is None:
        if len(candidates) == 1:
            return GateResolution(gate=candidates[0])
        options = "、".join(gate.difficulty for gate in candidates)
        first = candidates[0]
        return GateResolution(
            error=f"「{gate_map}」需要指定难度（可选：{options}），例如：卡战备 {first.map} {first.difficulty}",
            suggestions=tuple(candidates),
        )

    if _norm(difficulty_text) == "常规":
        return GateResolution(error=config.regular_note)

    if not any(_norm(difficulty_text) == _norm(item) for item in config.difficulties):
        options = "、".join(config.difficulties)
        return GateResolution(error=f"不认识难度「{difficulty_text}」（可选：{options}）")

    match = next((gate for gate in candidates if _norm(gate.difficulty) == _norm(difficulty_text)), None)
    if match is None:
        options = "、".join(gate.difficulty for gate in candidates)
        return GateResolution(
            error=f"{gate_map}没有{difficulty_text}难度，可选：{options}",
            suggestions=tuple(candidates),
        )
    return GateResolution(gate=match)


def _find_map(config: GateConfig, text: str) -> str | None:
    for gate in config.gates:
        if gate.matches_map(text):
            return gate.map
    return None
