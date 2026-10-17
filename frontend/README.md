# Find Your Pic — web UI

React (Vite) frontend for face search, upload, and photo grids.
In Docker Compose, `nginx` serves the production build and reverse-proxies
`/api/*` to the FastAPI container so the browser sees a single origin.

## Docker Compose (recommended)

From the **repo root**:

```powershell
Copy-Item .env.example .env   # edit FACE_SEARCH_DIR, PHOTO_ROOT, INSIGHTFACE_DIR
docker compose up --build
```

Open **http://127.0.0.1:8511**. Both ports are published to the loopback
interface only; there is no authentication in v1. Do not change the bindings
to `0.0.0.0`.

| Variable | Points at | Mounted as |
|---|---|---|
| `FACE_SEARCH_DIR` | folder containing `index.sqlite3` | `/data` in the API container |
| `PHOTO_ROOT` | indexed photo collection | `/photos` in the API container |
| `INSIGHTFACE_DIR` | `~/.insightface` model weights | `/root/.insightface` (read-only) |

## Local development

API must already be running on port 8000 (e.g. `python -m face_search.web`).

```powershell
cd apps/frontend
npm install
npm run dev        # http://127.0.0.1:5173, Vite proxies /api → 127.0.0.1:8000
```

## Environment variable

`VITE_API_BASE` is an empty string in Compose so the browser uses the
same-origin `/api` path proxied by nginx. For a separately hosted frontend,
set it to the Render backend URL at build time and add the frontend origin to
the backend's `CORS_ORIGINS`. See the root `deployment.md` for limitations
that must be addressed before exposing personal photos on the public internet.

## ONNX Runtime Web — intentionally excluded

Client-side (in-browser) face detection and embedding via ONNX Runtime Web
is **not implemented** in this version. Query photos are sent to the Python
API, which runs InsightFace on the server. This keeps the Docker image small,
avoids shipping ~50 MB of WASM/model files to the browser, and means one
inference code-path to maintain. Add ONNX Runtime Web in a later pass if
offline / edge detection is required.
