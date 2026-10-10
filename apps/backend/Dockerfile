FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY face_search ./face_search
COPY apps/backend ./apps/backend
COPY apps/__init__.py ./apps/__init__.py

# Install CPU-only ONNX Runtime (no CUDA dependency in the base image).
# GPU users can override by building with a CUDA base image and installing
# onnxruntime-gpu; the application code requires no changes.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir numpy Pillow tqdm insightface onnxruntime fastapi "uvicorn[standard]" httpx \
    && pip install --no-cache-dir --no-deps -e .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FACE_SEARCH_DB=/data/index.sqlite3 \
    PHOTO_ROOT=/photos \
    STORAGE_BACKEND=local

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "apps.backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
