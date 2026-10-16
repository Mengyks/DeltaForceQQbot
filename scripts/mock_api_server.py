"""本地假数据源：按接口路径与档位返回 tests/fixtures 里录制好的真实响应。

用途：在没有 token（或不想消耗平台资源）的情况下跑通"机器人 → 数据源 → 回复"的完整链路。
会把收到的请求参数打印出来（token 打码），便于核验缓存是否生效、参数是否正确。

    python scripts/mock_api_server.py --port 8799
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
REAL_FIXTURES = ROOT / "tests" / "fixtures" / "real"
V4_PATH = "/v1/sjz_api/jzv4_zb"
DIY_PATH = "/v1/sjz_api/jzv3_diy"

HITS: dict[str, int] = {}


def payload_for(path: str, params: dict[str, str]) -> dict:
    if path == V4_PATH:
        lv = params.get("lv", "0")
        fixture = REAL_FIXTURES / f"jzv4_zb_lv{lv}.json"
    elif path == DIY_PATH:
        fixture = ROOT / "tests" / "fixtures" / "jzv3_diy.json"
    else:
        return {"code": 404, "msg": f"mock 里没有这个接口：{path}"}

    if not fixture.exists():
        return {"code": 404, "msg": f"fixture 不存在：{fixture.name}（可用档位见 tests/fixtures/real）"}
    return json.loads(fixture.read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # 关掉默认访问日志，自己打
        pass

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802（BaseHTTPRequestHandler 要求的方法名）
        parsed = urlparse(self.path)

        if parsed.path == "/_stats":
            self._send_json(HITS)
            return

        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        masked = {k: ("***" if k == "token" else v) for k, v in params.items()}
        HITS[parsed.path] = HITS.get(parsed.path, 0) + 1
        print(f"[mock] {parsed.path} 第 {HITS[parsed.path]} 次调用 参数={masked}", flush=True)

        self._send_json(payload_for(parsed.path, params))


def main() -> None:
    parser = argparse.ArgumentParser(description="本地假数据源")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址；容器间联调时用 0.0.0.0",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mock] 假数据源已启动：http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
