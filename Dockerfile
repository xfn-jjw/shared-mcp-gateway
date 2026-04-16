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
      "pyyaml>=6.0,<7" \
      "mysql-connector-python>=9.0.0" \
      "python-dotenv>=1.0.1" \
      "python-frontmatter>=1.1.0" \
      "requests>=2.32.0"

COPY shared_mcp_gateway /app/shared_mcp_gateway
COPY scripts /app/scripts
COPY registry.compose.toml /app/registry.compose.toml

CMD ["python", "/app/shared_mcp_gateway/gateway.py", "--registry", "/app/registry.compose.toml", "--log-level", "INFO"]
