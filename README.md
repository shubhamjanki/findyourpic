# Find Your Pic (findyourpic)

Find Your Pic indexes faces in an image folder you own or are authorized to organize, then ranks images against one or more reference photos. It is intended for personal photo organization. Get consent from the people in the photos before indexing or searching them. Do not use it to identify or track people without their knowledge or permission.

Image files and face embeddings stay on this machine. There is no network search, telemetry, or runtime model download. InsightFace model files must already be installed locally before the first run.

## Install

Python 3.10 or newer is required. Create and activate a virtual environment, then install the package and its dependencies:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```
On Linux or macOS, use `python3 -m venv .venv`, activate with `source .venv/bin/activate`, and run `python -m pip install -e '.[dev]'`.

The default backend is InsightFace ArcFace. Install the InsightFace `buffalo_l` model pack into `~/.insightface/models/buffalo_l/` (the directory should contain its `.onnx` model files) before indexing. Obtain the model pack separately from a trusted source and observe its license. This application checks for local model files and will not fetch them. The configured ONNX Runtime package prefers CUDA when its provider is available and otherwise uses CPU; GPU use requires compatible NVIDIA drivers and CUDA libraries. For strict air-gapped deployment, install Python wheels and the model pack from approved local media.

### Platform notes

- **Windows:** use a supported 64-bit Python. InsightFace and ONNX Runtime wheels are generally the simplest backend path. The optional dlib dependency may require Visual Studio C++ Build Tools and a compatible CMake setup; prebuilt wheels must match your Python version and architecture.
- **Linux:** install the platform's compiler/build tools if pip must build native dependencies. CPU-only ONNX Runtime is sufficient; no CUDA setup is needed.
- **macOS:** use a Python version supported by ONNX Runtime wheels. On Apple Silicon, ensure Python, NumPy, and native packages all use the same architecture. dlib may need CMake and Xcode Command Line Tools.

To use the alternative dlib HOG backend, install its optional extra with `python -m pip install -e ".[dlib]"` and pass `--backend dlib`. dlib installation is platform-sensitive; if a source build fails, install compiler/CMake prerequisites or a matching trusted wheel. The two backends produce incompatible embedding dimensions, so switching backends rebuilds the index.

## Project Structure

```text
findyourpic/
├── frontend/             # Standalone React / Vite UI (Vercel, Cloudflare, Netlify)
├── backend/              # Standalone FastAPI Engine & InsightFace (Render, Railway, Fly.io)
├── tests/                # Automated Pytest suite
└── docker-compose.yml    # Full-stack local dev / VPS setup
```

## Docker Compose (local-only)

The default way to run the UI is a two-container layout that mirrors the future hosted split while staying on your machine. **Compose publishes both ports to the loopback interface only (`127.0.0.1`) and there is no authentication in this version; do not change the bindings or expose the containers to a network.**

```text
Browser
  └── http://127.0.0.1:8511
        ├── frontend_web_nginx    serves the React build; proxies /api/* to the API
        └── backend_api_FastAPI   /api config, photos, people, image, face, detect,
              │                   search, labels, feedback, zip, delete
              ├── face_search     index, search, InsightFace
              ├── SQLite          ~/.face_search/index.sqlite3 (mounted at /data)
              ├── photos          PHOTO_ROOT (mounted at /photos)
              └── models          ~/.insightface/models/buffalo_l (read-only)
```

Copy `.env.example` to `.env`, point the variables at your existing index, photo folder, and model pack, then build and start:

```powershell
Copy-Item .env.example .env   # then edit FACE_SEARCH_DIR, PHOTO_ROOT, INSIGHTFACE_DIR
docker compose up --build
```

The Compose API uses NVIDIA GPU acceleration for InsightFace indexing and face
search when Docker Desktop GPU support and compatible NVIDIA drivers are
available. The API container requests all available GPUs; without working GPU
passthrough, it will not start. For CPU-only use, remove `gpus: all` from the
API service in `docker-compose.yml` and replace `onnxruntime-gpu[cuda,cudnn]`
with `onnxruntime` in its Dockerfile.

Open `http://127.0.0.1:8511`. Environment variables:

| Variable | Host path | Container |
| --- | --- | --- |
| `FACE_SEARCH_DIR` | folder containing `index.sqlite3` | `/data` → `FACE_SEARCH_DB=/data/index.sqlite3` |
| `PHOTO_ROOT` | indexed photo collection | `/photos` |
| `INSIGHTFACE_DIR` | `~/.insightface` (model weights) | `/root/.insightface`, read-only |
| `S3_BROWSE_ENABLED` | `false` by default; set to `true` to enable the All photos → Browse S3 bucket button | API environment |
| `S3_BUCKET`, `AWS_REGION`, `AWS_PROFILE` | private bucket, its region, and a profile in `%USERPROFILE%\.aws` | API environment; AWS profile directory mounted read-only |
| `S3_PREFIX` | optional key prefix; blank browses supported images in the whole bucket | API environment |

The existing corpus is imported, not re-embedded: the SQLite database (its stored face embeddings), the photo folder, and the model pack are bind-mounted into the API container, so startup does not rescan or regenerate the ~4,500 face vectors. If you later add photos, update the index inside the container instead:

```text
docker compose exec api python -m face_search.cli index --images /photos --db /data/index.sqlite3 --workers 2
```

or use the Collection → Update index control, which indexes the configured photo root and shows indexing progress with an estimated time remaining. The API reads its database and photo root from the environment (`FACE_SEARCH_DB`, `PHOTO_ROOT`), never from client-supplied `db`/`collection` query parameters. Its CORS allowlist covers the local web origin only (`http://127.0.0.1:8511`). Local photo browsing and indexing use filesystem storage. An opt-in **Browse S3 bucket** control can display private S3 images through the backend when `S3_BROWSE_ENABLED=true`; it does not migrate or index those images. This unauthenticated feature is for trusted local use only. The private S3 smoke-upload endpoint is separate and also opt-in. See [deployment.md](deployment.md) for configuration and IAM requirements. Cloudflare R2 and Supabase adapters remain stubs.

## Browser UI

Without Docker, start the FastAPI app directly. When `apps/frontend/dist` exists it serves the React build; otherwise it falls back to the legacy static UI:

```powershell
python -m face_search.web --port 8511
```

Then open `http://127.0.0.1:8511`. The browser UI is a React (Vite) app with three screens — **Home** (text search, camera, upload), **All** (infinite photo grid plus a swipeable viewer), and **Collection** (people grid, editable names, and index controls against the server photo root). It includes a home photo search, similarity-grouped face collection, likes/dislikes with re-feedback, multi-select downloads (folder picker or ZIP), and editable person/collection names stored in the local index database. Face groups are approximate and use a cosine-similarity threshold; review groups and rename them as needed. Text search matches collection names first, opening a unique person or showing filtered collection results, then falls back to indexed photo paths. Tune similarity and result limit under Search settings. The UI server binds to localhost only; images and embeddings are not sent to a remote service.

The previous Streamlit interface remains available if its optional dependency is installed: `python -m pip install -e '.[ui]'`, then `streamlit run face_search/streamlit_app.py`.

See [architecture.md](architecture.md) for deployment options, current hosting limitations, and a staged frontend/API split plan. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) for contribution and vulnerability-reporting guidance.

## Repository and privacy

The project ignores local image collections, SQLite indexes, ONNX model weights, virtual environments, caches, and common local secrets. Review `git status` and inspect all staged files before pushing; ignore rules do not remove files that were already committed or force-added. Do not publish photos, face embeddings, or other personal data.

No project license has been selected. Until a license is chosen, GitHub visibility does not grant other people permission to reuse, modify, or redistribute the code. InsightFace and model-pack licensing is separate.

## Commands

The default database is `~/.face_search/index.sqlite3`. `--db` can select a different SQLite file.

Index or incrementally update a folder (supports jpg, jpeg, png, webp, and bmp):

```text
face-search index --images "C:\Users\me\Pictures" --db .\photos.sqlite3 --workers 2
```

Search by one query photo, interactively choosing a face if multiple are detected:

```text
face-search search --query .\reference.jpg --db .\photos.sqlite3 --threshold 0.45 --top 25
```

Average embeddings from multiple query photos and write machine-readable output; CSV is also available with `--format csv`:

```text
face-search search --query .\ref1.jpg --query .\ref2.jpg --db .\photos.sqlite3 --format json
```

Copy matching images while preserving their collection-relative folders, and create a boxed contact sheet:

```text
face-search search --query .\reference.jpg --db .\photos.sqlite3 --copy-to .\matches --contact-sheet .\matches.jpg
```

Show indexed file/face counts and source/backend metadata:

```text
face-search stats --db .\photos.sqlite3
```

Remove all indexed metadata and embeddings (the original photos are untouched):

```text
face-search clear --db .\photos.sqlite3
```

For a checkout without an installed console script, replace `face-search` with `python -m face_search.cli` after installing the package in editable mode.

## Threshold and accuracy

Cosine scores range from -1 to 1; higher means more similar. The default threshold is 0.45, but the useful value depends on the backend, camera, and collection. Start higher to favor precision, inspect the returned examples, then lower the threshold if true matches are missing. `--top` limits the number of distinct images returned after thresholding. A score is a similarity measure, not a probability or proof of identity.

Face matching is fallible. Lighting, pose, age changes, image quality, occlusion, and detector errors can reduce accuracy. Embedding models can also perform unevenly across demographic groups. Review results manually; do not use this tool for consequential decisions, surveillance, access control, or sole-source identity verification.

## Design decisions

- **Backends:** InsightFace ArcFace is the default for practical embedding quality; its model and ONNX execution are local, using CUDA when available and CPU otherwise. The Docker Compose API is configured for NVIDIA GPU acceleration. dlib/face_recognition remains an optional alternative behind the same `detect()`/`embed()` interface.
- **Index:** SQLite stores file metadata, one row per detected face, normalized float32 embeddings, and oriented-image bounding boxes. SHA-256 is checked alongside modification time and size, so same-timestamp content changes are still detected. SQLite keeps setup simple and is appropriate for local collections; indexing workers compute in separate processes while one parent process writes the database.
- **100k+ images:** use FAISS (or another ANN index) for vector candidate retrieval, retain SQLite as the durable metadata store, and add index versioning/rebuild and recall benchmarks. For a large collection, batch embedding, tune worker count for memory, and persist an ANN index to avoid repeatedly scanning all face vectors.

## Tests

The pytest suite uses synthetic images and a deterministic fake backend; it does not download model weights or require GPU/dlib:

```text
python -m pytest
```