FROM node:24-alpine AS frontend-builder

WORKDIR /frontend

# 先只复制前端依赖清单，利用 Docker 层缓存减少重复安装时间。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 再复制前端源码并执行构建，产物最终会被 Python 运行镜像直接托管。
COPY frontend ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace/mempalace

WORKDIR /app

COPY requirements.txt /app/requirements-gateway.txt
RUN python -m pip install --upgrade pip && \
    pip install -r /app/requirements-gateway.txt && \
    pip install \
      "chromadb>=0.5.0,<0.7" \
      "litellm>=1.70.0,<1.82.7" \
      "openai>=1.0.0" \
      "anthropic>=0.71.0" \
      "jsonschema>=4.25.0" \
      "pillow>=12.0.0" \
      "numpy>=1.24.0" \
      "colorama>=0.4.6" \
      "flask>=3.1.0" \
      "pyautogui>=0.9.54" \
      "pydantic>=2.12.0" \
      "pyyaml>=6.0,<7" \
      "mysql-connector-python>=9.0.0" \
      "python-dotenv>=1.0.1" \
      "python-frontmatter>=1.1.0" \
      "requests>=2.32.0"

COPY shared_mcp_gateway /app/shared_mcp_gateway
COPY scripts /app/scripts
COPY registry.compose.toml /app/registry.compose.toml
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

CMD ["python", "/app/shared_mcp_gateway/gateway.py", "--registry", "/app/registry.compose.toml", "--log-level", "INFO"]
