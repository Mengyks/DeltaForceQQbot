"""假 NapCat：以 OneBot v11 反向 WebSocket 的身份连接机器人，发消息并接收回复。

用途：在没有 QQ 账号、没有 NapCat 的情况下验证协议链路是否通
（WS 握手 → 事件解析 → 命令匹配 → 业务处理 → 发消息 API 调用）。

    python scripts/fake_onebot_e2e.py --uri ws://127.0.0.1:8099/onebot/v11/ws "卡战备 11" "凑战备 5万"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

SELF_ID = 10000
GROUP_ID = 30000


def summarize(message) -> str:
    """把 OneBot 消息段转成一行摘要：图片只显示类型与体积，不打印 base64。"""
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for segment in message or []:
        if not isinstance(segment, dict):
            parts.append(str(segment))
            continue
        if segment.get("type") == "image":
            data = str(segment.get("data", {}).get("file", ""))
            kind = data.split(":", 1)[0] if ":" in data else "?"
            parts.append(f"[图片 {kind}，{len(data)} 字符]")
        elif segment.get("type") == "text":
            parts.append(str(segment.get("data", {}).get("text", "")))
        else:
            parts.append(f"[{segment.get('type')}]")
    return " ".join(parts)


def group_message_event(text: str, index: int) -> dict:
    user_id = 20000 + index
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": int(time.time()),
        "self_id": SELF_ID,
        "message_id": 100 + index,
        "user_id": user_id,
        "group_id": GROUP_ID,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": user_id, "nickname": f"测试用户{index}", "card": "", "role": "member"},
    }


async def run(uri: str, texts: list[str], timeout: float, token: str = "") -> int:
    received = 0
    # NoneBot 的 OneBot 适配器在握手时要求 X-Self-ID 头（NapCat 会自动带上）
    headers = {
        "X-Self-ID": str(SELF_ID),
        "X-Client-Role": "Universal",
        "User-Agent": "OneBot/11 FakeNapCat",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with websockets.connect(uri, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                    "self_id": SELF_ID,
                }
            )
        )
        print(f"已作为假 NapCat 连上 {uri}")
        for index, text in enumerate(texts, start=1):
            print(f"\n>>> 发送群消息：{text}")
            await ws.send(json.dumps(group_message_event(text, index), ensure_ascii=False))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining(deadline))
            except TimeoutError:
                break
            message = json.loads(raw)
            # 注意：NoneBot 的 OneBot v11 适配器发消息用统一接口 send_msg，不是 send_group_msg
            if message.get("action") in {"send_msg", "send_group_msg", "send_private_msg"}:
                received += 1
                print("-" * 60)
                print(summarize(message["params"].get("message")))
                print("-" * 60)
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": received},
                            "echo": message.get("echo"),
                        }
                    )
                )
            elif message.get("action"):
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {},
                            "echo": message.get("echo"),
                        }
                    )
                )
            else:
                print(f"[原始报文] {raw[:300]}")
    return received


def remaining(deadline: float) -> float:
    return max(deadline - time.monotonic(), 0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description="假 NapCat 端到端联调")
    parser.add_argument("--uri", default="ws://127.0.0.1:8099/onebot/v11/ws")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--token", default="", help="与机器人 ONEBOT_ACCESS_TOKEN 一致（未配置则留空）")
    parser.add_argument("texts", nargs="*", default=["卡战备 11", "凑战备 5万 不要枪", "晚上吃什么"])
    args = parser.parse_args()

    count = asyncio.run(run(args.uri, args.texts, args.timeout, args.token))
    print(f"\n共收到 {count} 条机器人回复")


if __name__ == "__main__":
    main()
