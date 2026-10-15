FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 当前镜像只提供独立装载服务，不包含 LINK 订舱自动化或任何企业原始文件。
EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
