# promptcheck demo (demo/app.py). CPU only.
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

# Bake both base models (English 1.9 GB, multilingual) into the image so restarts do not
# download them.
RUN python -c "from huggingface_hub import snapshot_download as d; d('fastino/GLiNER2.5-Decide'); d('fastino/GLiNER2.5-multi-Decide')"

COPY poc/train_gliner.py poc/train_gliner.py
COPY models/gliner-erp/best models/gliner-erp/best
COPY models/gliner-erp-multi/best models/gliner-erp-multi/best
COPY demo demo

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DATA_DIR=/data

EXPOSE 8000
# One worker: each base model is shared by two contestants, and requests are serialised anyway.
CMD ["sh", "-c", "uvicorn demo.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
