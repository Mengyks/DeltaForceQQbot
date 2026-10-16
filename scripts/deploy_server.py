"""把项目同步到 Linux 服务器并在服务器上启动（本机 GitHub 通道受限时的替代路径）。

用法（在项目根目录执行）：

    # 1) 首次：把本机公钥装到服务器（之后就不需要密码了）
    DEPLOY_SSH_PASSWORD='服务器密码' python scripts/deploy_server.py --host 1.2.3.4 --user root --install-key

    # 2) 上传项目并在服务器上构建启动
    python scripts/deploy_server.py --host 1.2.3.4 --user root

    # 只上传不启动；或只启动不上传
    python scripts/deploy_server.py --host 1.2.3.4 --user root --no-up
    python scripts/deploy_server.py --host 1.2.3.4 --user root --no-upload

说明：
- 默认上传到服务器的 /opt/deltaforce-qqbot，可用 --path 修改。
- 会一并上传 .env（含 token，属于敏感文件，仅通过 SSH 传输，不会打进镜像）。
- 排除 .venv、__pycache__、.git、缓存目录等；`--exclude-tests` 可跳过测试与 fixture。
"""

from __future__ import annotations

import argparse
import os
import posixpath
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit("缺少依赖：请先执行 .venv/Scripts/python.exe -m pip install paramiko")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_EXCLUDES = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "napcat",
    "data",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".sqlite3"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
# 上传时跳过的相对路径前缀（本地预览产物、原型图）
SKIP_PREFIXES = ("assets/_preview", "assets/items/html_prototype")


def should_skip(rel: Path, *, exclude_tests: bool, skip_assets: bool = False) -> bool:
    parts = set(rel.parts)
    if parts & DEFAULT_EXCLUDES:
        return True
    if rel.name in SKIP_NAMES or rel.suffix in SKIP_SUFFIXES:
        return True
    rel_posix = rel.as_posix()
    if any(rel_posix.startswith(prefix) for prefix in SKIP_PREFIXES):
        return True
    if skip_assets and rel_posix.startswith("assets/"):
        return True
    if any(part.endswith(".egg-info") for part in rel.parts):
        return True
    if exclude_tests and rel.parts and rel.parts[0] in {"tests"}:
        return True
    return False


def iter_files(*, exclude_tests: bool, skip_assets: bool = False):
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        if should_skip(rel, exclude_tests=exclude_tests, skip_assets=skip_assets):
            continue
        yield path, rel


def connect(args) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    password = args.password or os.environ.get("DEPLOY_SSH_PASSWORD")
    key_path = Path(args.key or (Path.home() / ".ssh" / "id_ed25519"))

    kwargs: dict = {
        "hostname": args.host,
        "port": args.port,
        "username": args.user,
        "timeout": 20,
        "banner_timeout": 30,
        "auth_timeout": 30,
    }
    if password:
        # 给了密码就用密码（首次 --install-key 的场景）
        kwargs["password"] = password
        kwargs["look_for_keys"] = False
    elif key_path.exists():
        kwargs["key_filename"] = str(key_path)
        kwargs["look_for_keys"] = True
    else:
        raise SystemExit(
            f"既没有密码也没有私钥：请设置 DEPLOY_SSH_PASSWORD，或先 ssh-keygen 生成 {key_path}"
        )

    try:
        client.connect(**kwargs)
    except paramiko.AuthenticationException:
        if password:
            raise SystemExit("认证失败：密码不正确，或服务器未开放密码登录。") from None
        raise SystemExit(
            f"密钥认证失败（{key_path}）。先执行一次 --install-key 把公钥装到服务器，"
            "或通过 DEPLOY_SSH_PASSWORD 传入密码。"
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"连接 {args.host}:{args.port} 失败：{exc.__class__.__name__}: {exc}") from None
    return client


def run(client: paramiko.SSHClient, command: str, *, timeout: float = 900) -> tuple[int, str]:
    """在服务器上执行命令并回显输出，返回（退出码, 输出）。"""
    print(f"\n$ {command}")
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    stdin.close()
    output_lines: list[str] = []
    for line in iter(stdout.readline, ""):
        line = line.rstrip("\n")
        output_lines.append(line)
        print(f"  {line}")
    err = stderr.read().decode("utf-8", "replace").strip()
    if err:
        output_lines.append(err)
        print(f"  [stderr] {err}")
    code = stdout.channel.recv_exit_status()
    return code, "\n".join(output_lines)


def install_key(client: paramiko.SSHClient, args) -> None:
    pub_path = Path.home() / ".ssh" / "id_ed25519.pub"
    if not pub_path.exists():
        raise SystemExit(f"找不到公钥 {pub_path}，请先生成：ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519")
    pubkey = pub_path.read_text(encoding="utf-8").strip()

    command = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys && "
        f"grep -qxF '{pubkey}' ~/.ssh/authorized_keys || echo '{pubkey}' >> ~/.ssh/authorized_keys"
    )
    code, _ = run(client, command)
    if code != 0:
        raise SystemExit("安装公钥失败")
    code, _ = run(client, "grep -c . ~/.ssh/authorized_keys")
    print("\n公钥已安装，之后可以不带密码运行部署脚本。")


def upload(client: paramiko.SSHClient, args) -> int:
    sftp = client.open_sftp()
    run(client, f"mkdir -p {args.path}")
    run(client, f"mkdir -p {args.path}/config {args.path}/deltaforce_bot {args.path}/plugins {args.path}/scripts {args.path}/docs")

    count = 0
    total_bytes = 0
    client.exec_command  # noqa: B018  （避免误用）
    for local, rel in iter_files(exclude_tests=args.exclude_tests, skip_assets=args.skip_assets):
        remote = posixpath.join(args.path, rel.as_posix())
        remote_dir = posixpath.dirname(remote)
        try:
            sftp.stat(remote_dir)
        except OSError:
            # 逐级创建远端目录
            parts = remote_dir.split("/")
            for i in range(2, len(parts) + 1):
                sub = "/".join(parts[:i])
                if not sub:
                    continue
                try:
                    sftp.stat(sub)
                except OSError:
                    try:
                        sftp.mkdir(sub)
                    except OSError:
                        pass
        sftp.put(str(local), remote)
        count += 1
        total_bytes += local.stat().st_size

    sftp.close()
    print(f"\n已上传 {count} 个文件（{total_bytes / 1024:.0f} KB）到 {args.host}:{args.path}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="把项目部署到 Linux 服务器")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", help="不推荐写在命令行；建议用环境变量 DEPLOY_SSH_PASSWORD")
    parser.add_argument("--key", help="私钥路径，默认 ~/.ssh/id_ed25519")
    parser.add_argument("--path", default="/opt/deltaforce-qqbot")
    parser.add_argument("--install-key", action="store_true", help="把本机公钥装到服务器后退出")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--no-up", action="store_true", help="只上传不启动")
    parser.add_argument("--exclude-tests", action="store_true", help="不上传 tests/（服务器上不需要跑测试时）")
    parser.add_argument("--skip-assets", action="store_true", help="跳过 assets/（图片资源 55MB，只在图片变动时才需要传）")
    parser.add_argument("--service", default="bot", help="docker compose 启动的服务名，默认只起 bot")
    parser.add_argument("--cmd", help="只在服务器上执行一条命令后退出（排查问题用）")
    parser.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"), help="上传单个文件：--put 本地路径 服务器路径")
    parser.add_argument("--get", nargs=2, metavar=("REMOTE", "LOCAL"), help="下载单个文件：--get 服务器路径 本地路径")
    args = parser.parse_args()

    client = connect(args)
    try:
        if args.put:
            local, remote = args.put
            sftp = client.open_sftp()
            print(f"上传 {local} → {args.host}:{remote}")
            sftp.put(local, remote)
            sftp.close()
            print("上传完成")
            return

        if args.get:
            remote, local = args.get
            sftp = client.open_sftp()
            print(f"下载 {args.host}:{remote} → {local}")
            sftp.get(remote, local)
            sftp.close()
            print("下载完成")
            return

        if args.cmd:
            code, _ = run(client, args.cmd, timeout=1800)
            raise SystemExit(0 if code == 0 else 1)

        if args.install_key:
            install_key(client, args)
            return

        if not args.no_upload:
            upload(client, args)

        if args.no_up:
            print("\n已按 --no-up 跳过启动。")
            return

        started = time.time()
        code, _ = run(
            client,
            f"cd {args.path} && docker compose up -d --build {args.service}",
            timeout=1800,
        )
        if code != 0:
            raise SystemExit("服务器上构建/启动失败，请检查上面的输出")
        run(client, f"cd {args.path} && docker compose ps")
        print(f"\n部署完成，用时 {time.time() - started:.0f} 秒。")
        print(f"查看日志：ssh {args.user}@{args.host} 'cd {args.path} && docker compose logs -f bot'")
    finally:
        client.close()


if __name__ == "__main__":
    main()
