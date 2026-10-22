#!/usr/bin/env bash
# 服务器原生部署的幂等引导脚本（由 scripts/deploy_server.py 以 root 调用，也可手动执行）。
#
# 做的事：
#   1. 创建专用系统用户（默认 deltaforce）；
#   2. 校验 Python >= 3.13（系统版本不够时尝试 uv）；
#   3. 创建/修复 venv，按 pyproject.toml 哈希安装依赖（可编辑安装）；
#   4. 安装 Playwright Chromium、Chromium 系统依赖、文泉驿中文字体；
#   5. 修正项目/数据/浏览器缓存的权限。
#
# 手动执行示例：
#   PROJECT_DIR=/opt/deltaforce-qqbot SERVICE_USER=deltaforce \
#     bash scripts/server_bootstrap.sh
#
# 可选环境变量：
#   PYTHON_BIN=python3           指定 Python 解释器
#   PIP_INDEX_URL=...            pip 源，默认清华
#   PLAYWRIGHT_BROWSERS_PATH=... 浏览器缓存目录，默认 $PROJECT_DIR/.playwright
#   FORCE_BOOTSTRAP=1            强制重装依赖/系统依赖
#   SKIP_DEPS=1                  跳过 pip 与浏览器安装（只做权限/目录）

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/deltaforce-qqbot}"
SERVICE_USER="${SERVICE_USER:-deltaforce}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BROWSERS_DIR="${PLAYWRIGHT_BROWSERS_PATH:-$PROJECT_DIR/.playwright}"
DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
STAMP_DIR="$PROJECT_DIR/.deploy"
FORCE_BOOTSTRAP="${FORCE_BOOTSTRAP:-0}"
SKIP_DEPS="${SKIP_DEPS:-0}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$PROJECT_DIR/pyproject.toml" ] || die "找不到 $PROJECT_DIR/pyproject.toml（请先上传项目）"
[ -f "$PROJECT_DIR/bot.py" ] || die "找不到 $PROJECT_DIR/bot.py（请先上传项目）"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    command -v sudo >/dev/null 2>&1 || die "需要 root 或 sudo 权限"
    SUDO="sudo"
fi

# ---- 1. 专用用户 ----
if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
    $SUDO groupadd --system "$SERVICE_USER"
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "创建系统用户 $SERVICE_USER"
    $SUDO useradd --system --gid "$SERVICE_USER" --create-home --home-dir "/home/$SERVICE_USER" \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if [ ! -d "/home/$SERVICE_USER" ]; then
    $SUDO mkdir -p "/home/$SERVICE_USER"
    $SUDO chown "$SERVICE_USER:$SERVICE_USER" "/home/$SERVICE_USER"
fi

# ---- 2. Python >= 3.13 ----
log "检查 Python（要求 >= 3.13）"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
    if command -v uv >/dev/null 2>&1; then
        log "系统 Python 版本过低，使用 uv 安装 Python 3.13"
        uv python install 3.13
        PYTHON_BIN="$(uv python find 3.13)"
    else
        die "需要 Python >= 3.13：请安装 python3.13，或先安装 uv 后重试"
    fi
fi
"$PYTHON_BIN" -c 'import sys; print("Python:", sys.version.split()[0])'

# ---- 3. 目录与权限 ----
$SUDO mkdir -p "$PROJECT_DIR" "$DATA_DIR" "$BROWSERS_DIR" "$STAMP_DIR"
# 代码归 root，服务用户只读；数据与浏览器缓存归服务用户读写
$SUDO chown -R root:root "$PROJECT_DIR"
$SUDO chmod -R u=rwX,go=rX "$PROJECT_DIR"
$SUDO chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$BROWSERS_DIR" "$STAMP_DIR"
$SUDO chmod 750 "$DATA_DIR"
if [ -f "$PROJECT_DIR/.env" ]; then
    $SUDO sed -i 's/\r$//' "$PROJECT_DIR/.env"
    $SUDO chown root:"$SERVICE_USER" "$PROJECT_DIR/.env"
    $SUDO chmod 640 "$PROJECT_DIR/.env"
fi

# ---- 4. 系统依赖：中文字体 / venv ----
need_apt=0
if ! command -v fc-list >/dev/null 2>&1 || ! fc-list :lang=zh 2>/dev/null | grep -qi 'WenQuanYi'; then
    need_apt=1
fi
if ! "$PYTHON_BIN" -c 'import venv, ensurepip' 2>/dev/null; then
    need_apt=1
fi
if [ "$need_apt" -eq 1 ]; then
    log "通过 apt 安装中文字体、fontconfig、python3-venv"
    $SUDO apt-get update
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        fonts-wqy-zenhei fonts-wqy-microhei fontconfig python3-venv
    $SUDO fc-cache -f >/dev/null 2>&1 || true
fi

# ---- 5. venv 与 Python 依赖 ----
VENV_DIR="$PROJECT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -c 'import sys' 2>/dev/null; then
    log "创建 venv：$VENV_DIR"
    rm -rf "$VENV_DIR"
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        if command -v uv >/dev/null 2>&1; then
            log "python -m venv 失败，改用 uv venv"
            rm -rf "$VENV_DIR"
            uv venv --seed --python "$PYTHON_BIN" "$VENV_DIR"
        else
            die "创建 venv 失败：请安装 python3-venv 后重试"
        fi
    fi
fi
VENV_PY="$VENV_DIR/bin/python"

PYPROJECT_HASH="$(sha256sum "$PROJECT_DIR/pyproject.toml" | awk '{print $1}')"
PYPROJECT_STAMP="$STAMP_DIR/pyproject.sha256"
if [ "$SKIP_DEPS" != "1" ]; then
    if [ "$FORCE_BOOTSTRAP" = "1" ] || [ ! -f "$PYPROJECT_STAMP" ] || \
       [ "$(cat "$PYPROJECT_STAMP" 2>/dev/null || true)" != "$PYPROJECT_HASH" ]; then
        log "安装 Python 依赖（可编辑安装，含 render 依赖）"
        (
            cd "$PROJECT_DIR"
            PIP_INDEX_URL="$PIP_INDEX_URL" "$VENV_PY" -m pip install -e ".[render]"
        )
        printf '%s\n' "$PYPROJECT_HASH" > "$PYPROJECT_STAMP"
    else
        log "pyproject.toml 未变化，跳过 pip install（需要重装可设 FORCE_BOOTSTRAP=1）"
    fi
else
    log "SKIP_DEPS=1，跳过 Python 依赖与浏览器安装"
fi

# ---- 6. Playwright Chromium 与系统依赖 ----
if [ "$SKIP_DEPS" != "1" ]; then
    if [ "$FORCE_BOOTSTRAP" = "1" ] || ! compgen -G "$BROWSERS_DIR/chromium*" >/dev/null; then
        log "安装 Playwright Chromium 到 $BROWSERS_DIR"
        if [ -n "$SUDO" ]; then
            $SUDO -u "$SERVICE_USER" env \
                HOME="/home/$SERVICE_USER" \
                PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" \
                "$VENV_PY" -m playwright install chromium
        else
            env \
                HOME="/home/$SERVICE_USER" \
                PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" \
                "$VENV_PY" -m playwright install chromium
        fi
    else
        log "Playwright Chromium 已存在，跳过下载"
    fi

    PLAYWRIGHT_DEPS_STAMP="$STAMP_DIR/playwright-deps.ok"
    if [ "$FORCE_BOOTSTRAP" = "1" ] || [ ! -f "$PLAYWRIGHT_DEPS_STAMP" ]; then
        log "安装 Chromium 系统依赖（playwright install-deps）"
        PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" "$VENV_PY" -m playwright install-deps chromium
        touch "$PLAYWRIGHT_DEPS_STAMP"
    else
        log "Chromium 系统依赖已安装，跳过"
    fi

    # install-deps / 下载可能产生 root 文件，统一把缓存交给服务用户
    $SUDO chown -R "$SERVICE_USER:$SERVICE_USER" "$BROWSERS_DIR"
fi

log "原生部署引导完成"
printf '  项目目录: %s\n' "$PROJECT_DIR"
printf '  服务用户: %s\n' "$SERVICE_USER"
printf '  Python:   %s\n' "$("$VENV_PY" --version 2>&1)"
printf '  浏览器:   %s\n' "$BROWSERS_DIR"
printf '  数据目录: %s\n' "$DATA_DIR"
