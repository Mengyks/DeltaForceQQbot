"""把项目同步到 Linux 服务器并启动。

默认使用**原生部署**：上传源码 → 在服务器上创建/更新 venv → 安装依赖与
Playwright Chromium → 写入 systemd 单元并重启服务。这样不需要构建或传输
1.5 GB 的 Docker 镜像，也不依赖服务器访问 Docker Hub。

需要保留旧的 Docker Compose 路径时加 ``--docker``。

用法（在项目根目录执行）：

    # 1) 首次：把本机公钥装到服务器（之后就不需要密码了）
    DEPLOY_SSH_PASSWORD='服务器密码' python scripts/deploy_server.py --host 1.2.3.4 --user root --install-key

    # 2) 上传并在服务器上原生部署
    python scripts/deploy_server.py --host 1.2.3.4 --user root

    # 常用参数
    --skip-assets        跳过 assets/（56 MB，图片没变时不传）
    --exclude-tests      不上传 tests/
    --keep-env           不覆盖服务器上的 .env（本地 .env 不作为唯一来源）
    --skip-deps          只更新代码，不检查/安装 Python 依赖和浏览器
    --force-bootstrap    强制重装 Python 依赖与 Chromium 系统依赖
    --stop-old-docker    首次从 Docker 迁移时停止旧的 deltaforce-bot 容器（保留容器便于回退）
    --playwright-download-host=URL  Playwright 浏览器下载镜像（国内网络可换源）
    --no-restart         完成引导和 systemd 单元安装，但不重启服务
    --no-up              只上传，不引导、不启动
    --no-upload          只引导/重启，不重新上传
    --docker             使用旧的 docker compose 部署路径

说明：
- 默认上传到服务器的 /opt/deltaforce-qqbot，可用 --path 修改。
- 会一并上传 .env（含 token，属于敏感文件，仅通过 SSH 传输，不会进入版本库）；
  服务器上会设置为 root:<service-user> 640。
- 服务默认以非 root 用户 deltaforce 运行，监听 127.0.0.1:8099。
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shlex
import time
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit("缺少依赖：请先执行 .venv/Scripts/python.exe -m pip install paramiko")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_TEMPLATE = PROJECT_ROOT / "deploy" / "deltaforce-bot.service"

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
    ".deploy",
    ".playwright",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".sqlite3"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
# 上传时跳过的相对路径前缀（本地预览产物、原型图）
SKIP_PREFIXES = ("assets/_preview", "assets/items/html_prototype")

_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
_SERVICE_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def should_skip(
    rel: Path,
    *,
    exclude_tests: bool,
    skip_assets: bool = False,
    skip_env: bool = False,
) -> bool:
    if skip_env and rel.as_posix() == ".env":
        return True
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


def iter_files(*, exclude_tests: bool, skip_assets: bool = False, skip_env: bool = False):
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        if should_skip(
            rel,
            exclude_tests=exclude_tests,
            skip_assets=skip_assets,
            skip_env=skip_env,
        ):
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


def _put_env(sftp, local: Path, remote: str) -> None:
    """上传 .env 时统一成 LF：systemd EnvironmentFile 不会自动去掉 CRLF 的 \\r。"""
    data = local.read_bytes().replace(b"\r\n", b"\n")
    with sftp.file(remote, "wb") as remote_file:
        remote_file.write(data)


def upload(client: paramiko.SSHClient, args) -> int:
    sftp = client.open_sftp()
    run(client, f"mkdir -p {shlex.quote(args.path)}")
    for sub in ("config", "deploy", "deltaforce_bot", "docs", "plugins", "scripts"):
        run(client, f"mkdir -p {shlex.quote(posixpath.join(args.path, sub))}")

    count = 0
    total_bytes = 0
    for local, rel in iter_files(
        exclude_tests=args.exclude_tests,
        skip_assets=args.skip_assets,
        skip_env=args.keep_env,
    ):
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
        if rel.as_posix() == ".env":
            _put_env(sftp, local, remote)
        else:
            sftp.put(str(local), remote)
        count += 1
        total_bytes += local.stat().st_size

    sftp.close()
    print(f"\n已上传 {count} 个文件（{total_bytes / 1024:.0f} KB）到 {args.host}:{args.path}")
    return count


def render_service_unit(
    project_dir: str,
    service_user: str,
    template_path: Path = SERVICE_TEMPLATE,
) -> str:
    """把 systemd 单元模板里的占位符替换成实际路径/用户。"""
    text = Path(template_path).read_text(encoding="utf-8")
    text = text.replace("__PROJECT_DIR__", project_dir)
    text = text.replace("__SERVICE_USER__", service_user)
    if "__PROJECT_DIR__" in text or "__SERVICE_USER__" in text:  # pragma: no cover - 防御
        raise SystemExit("systemd 模板占位符替换失败")
    return text


def install_systemd_unit(client: paramiko.SSHClient, args, unit_text: str) -> str:
    service_name = args.service_name.removesuffix(".service")
    remote_tmp = f"/tmp/{service_name}.service"
    sftp = client.open_sftp()
    with sftp.file(remote_tmp, "w") as remote_file:
        remote_file.write(unit_text)
    sftp.close()
    code, _ = run(
        client,
        f"install -m 644 {shlex.quote(remote_tmp)} "
        f"/etc/systemd/system/{shlex.quote(service_name + '.service')} && "
        f"rm -f {shlex.quote(remote_tmp)}",
    )
    if code != 0:
        raise SystemExit("安装 systemd 单元失败")
    return service_name


def build_bootstrap_command(args) -> str:
    env = {
        "PROJECT_DIR": args.path,
        "SERVICE_USER": args.service_user,
        "PYTHON_BIN": args.python,
        "PLAYWRIGHT_BROWSERS_PATH": posixpath.join(args.path, ".playwright"),
        "FORCE_BOOTSTRAP": "1" if args.force_bootstrap else "0",
        "SKIP_DEPS": "1" if args.skip_deps else "0",
    }
    download_host = getattr(args, "playwright_download_host", "")
    if download_host:
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = download_host
    assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return (
        f"cd {shlex.quote(args.path)} && {assignments} "
        "bash scripts/server_bootstrap.sh"
    )


def stop_old_docker_container(client: paramiko.SSHClient, args) -> None:
    """迁移到原生部署时，旧容器会占用 8099；默认只检查并提示，加 --stop-old-docker 才停。"""
    if args.stop_old_docker:
        run(
            client,
            "docker stop deltaforce-bot 2>/dev/null || true",
        )
        return

    _, output = run(
        client,
        "if command -v docker >/dev/null 2>&1 && "
        "docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'deltaforce-bot'; "
        "then echo OLD_DOCKER_RUNNING; else echo NO_OLD_DOCKER; fi",
    )
    if "OLD_DOCKER_RUNNING" in output:
        raise SystemExit(
            "检测到旧 Docker 容器 deltaforce-bot 仍在运行，会占用 127.0.0.1:8099。\n"
            "首次切换请加 --stop-old-docker（只停止容器，保留它便于回退），"
            "或手动执行：docker stop deltaforce-bot。"
        )


def ensure_port_available(client: paramiko.SSHClient, service_name: str) -> None:
    """重启前确认 8099 没有被别的进程占用（例如旧的原生进程或未清理的容器）。"""
    _, output = run(
        client,
        f"if systemctl is-active --quiet {shlex.quote(service_name)}; then echo SERVICE_ACTIVE; "
        "elif ss -lntp 2>/dev/null | grep -q ':8099'; then echo PORT_BUSY; "
        "else echo PORT_FREE; fi",
    )
    if "PORT_BUSY" in output:
        run(client, "ss -lntp 2>/dev/null | grep ':8099' || true")
        raise SystemExit(
            "127.0.0.1:8099 已被其他进程占用。请先停掉旧进程（旧 Docker 容器可加 "
            "--stop-old-docker；旧原生进程请 systemctl stop 或 kill），再重新部署。"
        )


def deploy_native(client: paramiko.SSHClient, args) -> None:
    if not args.no_upload:
        upload(client, args)

    if args.no_up:
        print("\n已按 --no-up 跳过引导与启动。")
        return

    started = time.time()
    code, _ = run(client, build_bootstrap_command(args), timeout=1800)
    if code != 0:
        raise SystemExit("服务器引导失败，请检查上面的输出")

    unit_text = render_service_unit(args.path, args.service_user)
    service_name = install_systemd_unit(client, args, unit_text)

    # 放在重启前：引导失败时旧容器仍在服务，不会把线上打空
    if not args.no_restart:
        stop_old_docker_container(client, args)
        ensure_port_available(client, service_name)

    reload_command = f"systemctl daemon-reload && systemctl enable {shlex.quote(service_name)}"
    if not args.no_restart:
        reload_command += f" && systemctl restart {shlex.quote(service_name)}"
    code, _ = run(client, reload_command)
    if code != 0:
        raise SystemExit("systemd 操作失败，请检查上面的输出")

    if not args.no_restart:
        # restart 成功不代表进程能保持运行，稍等后确认一次
        time.sleep(3)
        code, _ = run(client, f"systemctl is-active --quiet {shlex.quote(service_name)}")
        if code != 0:
            run(client, f"journalctl -u {shlex.quote(service_name)} -n 60 --no-pager || true")
            raise SystemExit(f"{service_name} 没有保持运行，请检查上面的日志")

    run(client, f"systemctl --no-pager --full status {shlex.quote(service_name)} || true")
    run(client, f"journalctl -u {shlex.quote(service_name)} -n 40 --no-pager || true")
    print(f"\n原生部署完成，用时 {time.time() - started:.0f} 秒。")
    print(
        f"查看日志：ssh {args.user}@{args.host} "
        f"'journalctl -u {service_name} -f'"
    )


def deploy_docker(client: paramiko.SSHClient, args) -> None:
    """旧的 Docker Compose 部署路径，保留用于回退/对照。"""
    if not args.no_upload:
        upload(client, args)

    if args.no_up:
        print("\n已按 --no-up 跳过启动。")
        return

    started = time.time()
    code, _ = run(
        client,
        f"cd {shlex.quote(args.path)} && docker compose up -d --build {shlex.quote(args.service)}",
        timeout=1800,
    )
    if code != 0:
        raise SystemExit("服务器上构建/启动失败，请检查上面的输出")
    run(client, f"cd {shlex.quote(args.path)} && docker compose ps")
    print(f"\nDocker 部署完成，用时 {time.time() - started:.0f} 秒。")
    print(f"查看日志：ssh {args.user}@{args.host} 'cd {args.path} && docker compose logs -f bot'")


def validate_args(parser: argparse.ArgumentParser, args) -> None:
    if not args.path.startswith("/"):
        parser.error("--path 必须是绝对路径（例如 /opt/deltaforce-qqbot）")
    if any(ch.isspace() for ch in args.path):
        parser.error("--path 不能包含空白字符（systemd ExecStart 不支持未转义的空格路径）")
    if not _SERVICE_USER_RE.match(args.service_user):
        parser.error("--service-user 只能包含小写字母、数字、_、-，且不能以数字开头")
    service_name = args.service_name.removesuffix(".service")
    if not service_name or not _SERVICE_NAME_RE.match(service_name):
        parser.error("--service-name 只能包含字母、数字、_、.、@、-")


def main() -> None:
    parser = argparse.ArgumentParser(description="把项目部署到 Linux 服务器（默认原生 systemd）")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", help="不推荐写在命令行；建议用环境变量 DEPLOY_SSH_PASSWORD")
    parser.add_argument("--key", help="私钥路径，默认 ~/.ssh/id_ed25519")
    parser.add_argument("--path", default="/opt/deltaforce-qqbot")
    parser.add_argument("--install-key", action="store_true", help="把本机公钥装到服务器后退出")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--no-up", action="store_true", help="只上传，不引导、不启动")
    parser.add_argument("--no-restart", action="store_true", help="完成引导和单元安装，但不重启服务")
    parser.add_argument("--exclude-tests", action="store_true", help="不上传 tests/（服务器上不需要跑测试时）")
    parser.add_argument("--skip-assets", action="store_true", help="跳过 assets/（图片资源 56MB，只在图片变动时才需要传）")
    parser.add_argument("--keep-env", action="store_true", help="不覆盖服务器上已有的 .env")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 pip 与 Playwright 安装，只更新代码")
    parser.add_argument("--force-bootstrap", action="store_true", help="强制重装依赖与 Chromium 系统依赖")
    parser.add_argument(
        "--stop-old-docker",
        action="store_true",
        help="首次从 Docker 迁移时，停止旧的 deltaforce-bot 容器（保留容器便于回退）",
    )
    parser.add_argument("--service", default="bot", help="[--docker] docker compose 的服务名，默认 bot")
    parser.add_argument("--service-name", default="deltaforce-bot", help="原生 systemd 服务名，默认 deltaforce-bot")
    parser.add_argument("--service-user", default="deltaforce", help="原生服务运行用户，默认 deltaforce")
    parser.add_argument("--python", default="python3", help="服务器上的 Python 解释器，默认 python3")
    parser.add_argument(
        "--playwright-download-host",
        default="",
        help="Playwright 浏览器下载镜像（默认官方；国内可试 https://npmmirror.com/mirrors/playwright）",
    )
    parser.add_argument("--cmd", help="只在服务器上执行一条命令后退出（排查问题用）")
    parser.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"), help="上传单个文件：--put 本地路径 服务器路径")
    parser.add_argument("--get", nargs=2, metavar=("REMOTE", "LOCAL"), help="下载单个文件：--get 服务器路径 本地路径")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--native", action="store_true", help="原生 systemd 部署（默认）")
    mode.add_argument("--docker", action="store_true", help="使用旧的 docker compose 部署")
    args = parser.parse_args()
    validate_args(parser, args)

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

        if args.docker:
            deploy_docker(client, args)
        else:
            deploy_native(client, args)
    finally:
        client.close()


if __name__ == "__main__":
    main()
