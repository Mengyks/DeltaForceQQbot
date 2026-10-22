"""部署脚本的纯逻辑测试（不连服务器）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from tests.support import PROJECT_ROOT

_SPEC = importlib.util.spec_from_file_location(
    "deploy_server", PROJECT_ROOT / "scripts" / "deploy_server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
deploy_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(deploy_server)


def test_render_service_unit_replaces_placeholders() -> None:
    unit = deploy_server.render_service_unit("/opt/deltaforce-qqbot", "deltaforce")

    assert "User=deltaforce" in unit
    assert "Group=deltaforce" in unit
    assert "WorkingDirectory=/opt/deltaforce-qqbot" in unit
    assert "ExecStart=/opt/deltaforce-qqbot/.venv/bin/python /opt/deltaforce-qqbot/bot.py" in unit
    assert "EnvironmentFile=-/opt/deltaforce-qqbot/.env" in unit
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/deltaforce-qqbot/.playwright" in unit
    assert "__PROJECT_DIR__" not in unit
    assert "__SERVICE_USER__" not in unit


def test_build_bootstrap_command_passes_native_settings() -> None:
    args = SimpleNamespace(
        path="/opt/deltaforce-qqbot",
        service_user="deltaforce",
        python="python3",
        force_bootstrap=True,
        skip_deps=True,
        playwright_download_host="https://npmmirror.com/mirrors/playwright",
    )

    command = deploy_server.build_bootstrap_command(args)

    assert command.startswith("cd /opt/deltaforce-qqbot && ")
    assert "PROJECT_DIR=/opt/deltaforce-qqbot" in command
    assert "SERVICE_USER=deltaforce" in command
    assert "FORCE_BOOTSTRAP=1" in command
    assert "SKIP_DEPS=1" in command
    assert "PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright" in command
    assert command.endswith("bash scripts/server_bootstrap.sh")


def test_upload_skips_generated_and_optional_paths() -> None:
    def skip(path: str, **kwargs) -> bool:
        kwargs.setdefault("exclude_tests", False)
        return deploy_server.should_skip(Path(path), **kwargs)

    assert skip(".deploy/pyproject.sha256")
    assert skip(".playwright/chromium-1234/chrome-linux/chrome")
    assert skip("data/chat.db")
    assert skip("__pycache__/x.pyc")
    assert skip("assets/_preview/card.png")
    assert skip("tests/test_commands.py", exclude_tests=True)
    assert not skip("tests/test_commands.py")
    assert skip(".env", skip_env=True)
    assert not skip(".env")
    assert not skip("deltaforce_bot/config.py")
