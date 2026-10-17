FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml* ./
COPY face_search ./face_search
COPY . ./

# Install ONNX Runtime with its CUDA and cuDNN runtime dependencies.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir numpy Pillow tqdm insightface fastapi "uvicorn[standard]" httpx boto3 \
    && pip uninstall --yes onnxruntime \
    && pip install --no-cache-dir "onnxruntime-gpu[cuda,cudnn]"

RUN mkdir -p /root/.insightface/models/buffalo_l \
    && python -c "import urllib.request, zipfile, io; res = urllib.request.urlopen('https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip'); zipfile.ZipFile(io.BytesIO(res.read())).extractall('/root/.insightface/models/buffalo_l')" || true

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FACE_SEARCH_DB=/data/index.sqlite3 \
    STORAGE_BACKEND=s3 \
    S3_BROWSE_ENABLED=true

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
