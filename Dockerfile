# "Break the ERP assistant" demo (demo/app.py). CPU only.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, from the lockfile, so code changes do not reinstall torch.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --group demo --no-install-project && rm -rf /root/.cache

# Bake the 1.9 GB base model into the image so restarts do not download it.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('fastino/GLiNER2.5-Decide')"

COPY poc/train_gliner.py poc/train_gliner.py
COPY models/gliner-erp/best models/gliner-erp/best
COPY demo demo

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DATA_DIR=/data

EXPOSE 8000
# One worker: both contestants share one model, and requests are serialised anyway.
CMD ["sh", "-c", "uvicorn demo.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
