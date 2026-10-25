"""制造台利润业务测试：解析、选最高、四台编排、错误处理与文本排版。"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltaforce_bot.adapters.errors import DataSourceAuthError, DataSourceError, QuotaExhausted
from deltaforce_bot.models.crafting import (
    CRAFTING_LEVEL,
    STATIONS,
    CraftingItem,
    CraftingResult,
    CraftingStationResult,
)
from deltaforce_bot.services.crafting import (
    CRAFTING_PATH,
    CraftingService,
    CraftingUnavailable,
    format_crafting_text,
    parse_items,
    select_top,
)
from tests.support import load_data, make_settings


class FakeCraftingClient:
    def __init__(
        self,
        payloads: dict[int, object] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self.payloads = payloads or {}
        self.errors = errors or {}
        self.calls: list[dict] = []

    async def get(self, path: str, *, params=None, ttl: int, cost: int = 1):
        station = int((params or {}).get("t") or 0)
        self.calls.append(
            {
                "path": path,
                "params": dict(params or {}),
                "ttl": ttl,
                "cost": cost,
                "station": station,
            }
        )
        if station in self.errors:
            raise self.errors[station]
        return self.payloads.get(station)


def fixtures() -> dict[int, object]:
    return {
        station: load_data(f"crafting_t{station}_l3.json", real=True)
        for station, _ in STATIONS
    }


def make_service(tmp_path: Path, **overrides):
    settings = make_settings(
        crafting_enabled=True,
        crafting_cache_ttl=43200,
        crafting_image_max_kb=350,
        **overrides,
    )
    client = FakeCraftingClient(fixtures())
    return CraftingService(settings, client), client  # type: ignore[arg-type]


def test_item_and_parse_fields() -> None:
    raw = load_data("crafting_t1_l3.json", real=True)[0]
    item = CraftingItem.from_api(raw)
    assert item.name
    assert item.period >= 0
    if item.period:
        assert item.price_hour == int(item.price / item.period)
    assert item.pic.startswith("https://")

    parsed = parse_items([raw, {"name": ""}, "bad"])
    assert len(parsed) == 1
    assert parse_items(None) == []
    assert parse_items(raw) == [item]


def test_select_top_by_hourly_then_total() -> None:
    items = [
        CraftingItem(name="a", grade=4, period=1, price=100, price_hour=100),
        CraftingItem(name="b", grade=4, period=1, price=500, price_hour=200),
        CraftingItem(name="c", grade=4, period=2, price=600, price_hour=200),
    ]
    top = select_top(items)
    assert top is not None and top.name == "c"
    assert select_top([]) is None


async def test_service_fetches_four_stations_and_selects_top(tmp_path: Path) -> None:
    service, client = make_service(tmp_path)
    result = await service.get_profit()

    assert result.complete is True
    assert result.available_count == 4
    assert result.fetched_at is not None
    assert [station.label for station in result.stations] == [label for _, label in STATIONS]
    assert [station.item.name for station in result.stations if station.item] == [
        "PERST-7蓝色激光镭指",
        ".300BLK_5",
        "高级头盔维修组合",
        "重型突击背心",
    ]
    assert [station.item.price_hour for station in result.stations if station.item] == [
        7944,
        40806,
        10308,
        30193,
    ]
    assert len(client.calls) == 4
    for call in client.calls:
        assert call["path"] == CRAFTING_PATH
        assert call["params"]["l"] == CRAFTING_LEVEL
        assert call["ttl"] == 43200
        assert call["cost"] == 1
        assert call["params"]["t"] == call["station"]


async def test_partial_failure_keeps_other_stations(tmp_path: Path) -> None:
    settings = make_settings(crafting_enabled=True, crafting_cache_ttl=43200)
    client = FakeCraftingClient(
        fixtures(),
        errors={2: DataSourceError("workbench down")},
    )
    service = CraftingService(settings, client)  # type: ignore[arg-type]

    result = await service.get_profit()
    assert result.complete is False
    assert result.available_count == 3
    assert result.stations[1].item is None
    text = format_crafting_text(result)
    assert "工作台：暂时获取失败" in text
    assert "12 小时缓存" in text


async def test_auth_and_quota_raise_unavailable(tmp_path: Path) -> None:
    settings = make_settings(crafting_enabled=True)
    service = CraftingService(
        settings,
        FakeCraftingClient(fixtures(), errors={1: DataSourceAuthError("bad token")}),  # type: ignore[arg-type]
    )
    with pytest.raises(CraftingUnavailable):
        await service.get_profit()

    service = CraftingService(
        settings,
        FakeCraftingClient(fixtures(), errors={1: QuotaExhausted("no quota")}),  # type: ignore[arg-type]
    )
    with pytest.raises(CraftingUnavailable):
        await service.get_profit()


async def test_format_text_and_cache_key(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    result = await service.get_profit()
    text = format_crafting_text(result)
    assert "【每日利润 · Lv3】" in text
    assert "技术中心：PERST-7蓝色激光镭指" in text
    assert "单次利润 +35,752" in text
    assert "每小时 +7,944" in text
    assert "数据来源：数据帝 Pro" in text
    assert result.cache_key().startswith("crafting:")
    assert result.cache_key() == result.cache_key()


def test_result_helpers() -> None:
    item = CraftingItem(name="x", grade=4, period=4, price=100, price_hour=25)
    result = CraftingResult(
        (
            CraftingStationResult(1, "技术中心", item),
            CraftingStationResult(2, "工作台", None, "down"),
        )
    )
    assert result.complete is False
    assert result.available_count == 1
    assert result.signature()[0][1] == "x"
    assert "2:-" in result.cache_key()


async def test_runtime_wires_crafting_service(tmp_path: Path) -> None:
    from deltaforce_bot.runtime import Runtime

    runtime = Runtime(make_settings(crafting_enabled=True, crafting_cache_ttl=43200))
    try:
        assert runtime.crafting is not None
        assert runtime.crafting_assets is not None
        assert runtime.crafting_assets.stats["supplies"] > 0
    finally:
        await runtime.aclose()

    runtime = Runtime(make_settings(crafting_enabled=False))
    try:
        assert runtime.crafting is None
    finally:
        await runtime.aclose()
