# 简核 · 生产镜像
# 没有 pip install —— 全站零第三方依赖，镜像 ~130MB，构建 5 秒
FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY web/ ./web/
COPY data/ ./data/
COPY server.py ./

# 数据落在挂载卷里，容器重建不丢用户
VOLUME ["/app/data"]
ENV JH_HOST=0.0.0.0 \
    JH_PORT=8848 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8848
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8848/api/health').read()" || exit 1

CMD ["python", "server.py"]
