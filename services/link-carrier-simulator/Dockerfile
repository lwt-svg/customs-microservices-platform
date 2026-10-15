FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 仅用于本地学习环境。真实承运人账号、真实站点和真实订舱数据均不属于本镜像范围。
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
