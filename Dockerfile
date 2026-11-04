# 物品使用登记系统 · Dockerfile
# 多阶段：依赖缓存 + 最终 gunicorn 生产模式
FROM python:3.11-slim AS builder

WORKDIR /app

# 先 pip install（利用 Docker 缓存层，requirements.txt 不变就不重跑）
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# === 最终镜像 ===
FROM python:3.11-slim

WORKDIR /app

# ===== 中文 locale（拼音排序）=====
RUN apt-get update && apt-get install -y --no-install-recommends locales \
    && sed -i 's/^# *\(zh_CN.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen zh_CN.UTF-8 \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8

# 仅拷贝装好的包
COPY --from=builder /install /usr/local

# 应用代码
COPY app.py .
COPY templates ./templates
COPY static ./static
# 可选：Dockerfile 和 docker-compose.yml 保留在仓库，不拷进镜像

# 创建数据目录 + 日志目录（Docker 进程以 root 运行，默认写 /app/data）
RUN mkdir -p /app/data/uploads && chmod 755 /app/data

# 环境变量（生产部署时可通过 docker-compose.yml 覆盖）
ENV DB_PATH=/app/data/items.db \
    PORT=5000 \
    SECRET_KEY=change-me-in-production \
    ADMIN_USERNAME=admin \
    ADMIN_PASSWORD=change-me-in-production

EXPOSE 5000

# 健康检查（每 30s ping /login，3 次失败算 unhealthy）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/login')" || exit 1

# 生产模式 gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "2", "--threads", "4", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--timeout", "60", "app:app"]
