FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml* ./
COPY face_search ./face_search
COPY . ./

# Install CPU-optimized ONNX Runtime (low memory footprint for cloud deployment)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir numpy Pillow tqdm insightface fastapi "uvicorn[standard]" httpx boto3 onnxruntime

# Pre-download lightweight buffalo_s model (15 MB vs 300 MB, uses ~80 MB RAM instead of >1GB)
RUN mkdir -p /root/.insightface/models/buffalo_s /data/.insightface/models/buffalo_s \
    && python -c "import urllib.request, zipfile, io; res = urllib.request.urlopen('https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip'); z = zipfile.ZipFile(io.BytesIO(res.read())); z.extractall('/root/.insightface/models/buffalo_s'); z.extractall('/data/.insightface/models/buffalo_s')" || true

# Pre-seed SQLite index with S3 face embeddings (so Render starts with all photos indexed)
COPY index.sqlite3 /data/index.sqlite3

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FACE_SEARCH_DB=/data/index.sqlite3 \
    STORAGE_BACKEND=s3 \
    S3_BROWSE_ENABLED=true \
    INSIGHTFACE_MODEL=buffalo_s \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
