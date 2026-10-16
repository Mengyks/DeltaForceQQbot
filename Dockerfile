# 三角洲行动 QQ 机器人 —— 运行镜像
#
# 构建：docker build -t deltaforce-qqbot:0.1.0 .
# 运行：见 docker-compose.yml（推荐）或 README 的部署章节
#
# 镜像里不含 .env（密钥在运行时通过 env_file 注入），也不含测试与联调脚本。

FROM python:3.13-slim

# 国内网络访问 PyPI 不稳定，默认走清华镜像（构建依赖与运行依赖都会用它）；
# 需要官方源时：docker build --build-arg PIP_INDEX=https://pypi.org/simple .
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX} \
    PIP_NO_CACHE_DIR=1 \
    PIP_RETRIES=5 \
    PIP_TIMEOUT=30 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 运行期系统依赖：
# - chromium：卡片渲染用的无头浏览器（配合 RENDER_CHROMIUM_PATH）
# - fonts-wqy-zenhei / microhei：中文字体，缺了会把中文渲染成方块
# 国内构建：把 Debian 源换成清华镜像（Debian 12/13 的 deb822 与经典 sources.list 都覆盖）
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖先装，改业务代码时可复用这一层缓存
COPY pyproject.toml README.md ./
COPY deltaforce_bot ./deltaforce_bot
RUN pip install --no-cache-dir ".[render]"

# 运行期文件
COPY bot.py ./
COPY plugins ./plugins
COPY config ./config

# 非 root 运行
RUN useradd --create-home --uid 10001 botuser && chown -R botuser:botuser /app
USER botuser

# 容器内必须监听 0.0.0.0 才能被 NapCat 容器访问（由 compose 注入 HOST/PORT）
ENV HOST=0.0.0.0 \
    PORT=8099
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8099), 3).close()"

CMD ["python", "bot.py"]
