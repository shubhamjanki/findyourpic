# Deployment & Platform Setup Guide

This guide details how to deploy **Find Your Pic** (`findyourpic`) across modern hosting platforms. The project is structured with clean, independent `frontend` and `backend` directories so that each service can be deployed independently to the best-suited platform (e.g., **Vercel / Cloudflare Pages** for frontend, and **Render / Railway / Fly.io / Docker** for backend).

---

## Architecture & Folder Structure

```text
findyourpic/
├── frontend/                     # Standalone React (Vite) Single Page Application
│   ├── src/                      # UI components, state, API client
│   ├── public/                   # Static icons and assets
│   ├── index.html                # HTML entry point
│   ├── package.json              # NPM scripts and dependencies
│   ├── vite.config.js            # Vite build configuration & dev proxy
│   ├── vercel.json               # SPA routing & API rewrites for Vercel
│   ├── nginx.conf                # Nginx proxy config for container builds
│   └── Dockerfile                # Multi-stage production Nginx container
│
├── backend/                      # Standalone FastAPI Backend & Face Search Engine
│   ├── face_search/              # InsightFace detection, ONNX embeddings, SQLite index
│   │   ├── backends/             # InsightFace / Dlib backends
│   │   ├── cli.py                # CLI indexing and search tool
│   │   ├── index.py              # Incremental indexing engine
│   │   └── search.py             # Cosine similarity vector search
│   ├── app.py                    # FastAPI application & REST endpoints
│   ├── service.py                # Face clustering, caching, UI config
│   ├── storage.py                # Local filesystem & private S3 image access
│   ├── settings.py               # Environment configuration loader
│   ├── requirements.txt          # Production Python dependencies
│   ├── pyproject.toml            # Python packaging metadata
│   └── Dockerfile                # Standalone CUDA/CPU backend container
│
├── tests/                        # Automated Pytest suite
│   ├── test_indexing.py
│   ├── test_s3_storage.py
│   ├── test_search.py
│   └── test_web.py
│
├── docker-compose.yml            # Multi-container orchestration (local or VPS)
├── .env.example                  # Environment variable reference template
├── .gitignore                    # Git rules (excludes images, secrets, builds)
└── README.md                     # Project overview and quick start
```

---

## 1. Frontend Deployment (Vercel, Cloudflare Pages, Netlify)

The frontend is a standard Vite React application. It can be hosted on any static hosting provider.

### Option A: Vercel (Recommended for Frontend)

1. Push your repository to GitHub (`https://github.com/shubhamjanki/findyourpic.git`).
2. Go to [Vercel Dashboard](https://vercel.com/) and click **Add New Project**.
3. Import your `findyourpic` repository.
4. In the project configuration:
   - **Root Directory**: Click edit and select `frontend`.
   - **Framework Preset**: `Vite` (automatically detected).
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
5. Under **Environment Variables**, add:
   - `VITE_API_BASE`: The URL of your deployed backend (e.g. `https://findyourpic-api.onrender.com`).
6. Click **Deploy**.

> [!TIP]
> The included `frontend/vercel.json` already contains single-page routing configuration so page reloads won't result in 404s.

---

### Option B: Cloudflare Pages

1. Log in to the Cloudflare dashboard and navigate to **Workers & Pages** > **Create application** > **Pages** > **Connect to Git**.
2. Select your repository.
3. Configure build settings:
   - **Root directory**: `frontend`
   - **Build command**: `npm run build`
   - **Build output directory**: `dist`
4. Set environment variable: `VITE_API_BASE` pointing to your backend URL.
5. Deploy.

---

### Option C: Netlify

1. In Netlify, select **Add new site** > **Import an existing project**.
2. Select GitHub and choose `findyourpic`.
3. Set:
   - **Base directory**: `frontend`
   - **Build command**: `npm run build`
   - **Publish directory**: `frontend/dist`
4. Add environment variable `VITE_API_BASE` under Site settings.
5. Deploy.

---

## 2. Backend Deployment (Render, Railway, Fly.io)

The backend runs FastAPI with InsightFace and ONNX Runtime. Because InsightFace model weights and photo embeddings require sufficient memory:
- Allocate an instance with **at least 2 GB RAM** (4 GB recommended for large photo sets).
- Attach a **persistent disk** to store `/data/index.sqlite3` and `/photos`.

---

### Option A: Render Web Service (Docker)

1. In Render Dashboard, click **New +** > **Web Service**.
2. Connect your GitHub repository.
3. Configure the service:
   - **Name**: `findyourpic-api`
   - **Language / Runtime**: `Docker`
   - **Root Directory**: `backend`
   - **Dockerfile Path**: `Dockerfile` (or `backend/Dockerfile` if root directory is empty)
   - **Docker Context**: `backend` (or `.` if deploying from repo root)
4. Choose an instance with at least **2 GB RAM**.
5. Add a **Persistent Disk**:
   - **Mount Path**: `/data`
   - **Size**: 5 GB+ (depending on your photo collection size)
6. Configure **Environment Variables**:

   | Variable | Value | Description |
   | --- | --- | --- |
   | `FACE_SEARCH_DB` | `/data/index.sqlite3` | SQLite index on persistent disk |
   | `PHOTO_ROOT` | `/data/photos` | Photo directory on persistent disk |
   | `HOME` | `/data` | Sets InsightFace model cache to `/data/.insightface` |
   | `STORAGE_BACKEND` | `local` | Local disk storage (or `s3`) |
   | `CORS_ORIGINS` | `https://your-frontend.vercel.app` | Exact URL of your deployed frontend |
   | `PORT` | `8000` | Render assigns this automatically |

7. **Health Check Path**: `/api/health`
8. Deploy the service.

---

### Option B: Railway

1. In Railway, click **New Project** > **Deploy from GitHub repo**.
2. Under service settings:
   - Set **Root Directory** to `/backend`.
3. Add a **Volume** mounted at `/data`.
4. Configure variables:
   - `FACE_SEARCH_DB=/data/index.sqlite3`
   - `PHOTO_ROOT=/data/photos`
   - `STORAGE_BACKEND=local`
   - `CORS_ORIGINS=https://your-frontend.vercel.app`
5. Railway will automatically build using `backend/Dockerfile` and expose the service with SSL.

---

### Option C: Fly.io

1. Install `flyctl` and run from the `backend/` directory:
   ```bash
   cd backend
   fly launch
   ```
2. Create a persistent volume for data:
   ```bash
   fly volumes create face_data --size 5
   ```
3. Set memory to at least 2GB:
   ```bash
   fly scale memory 2048
   ```
4. Deploy with `fly deploy`.

---

## 3. Local Full-Stack Deployment (Docker Compose)

For local development or hosting on a self-managed VPS:

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Set your paths in `.env`:
   ```env
   FACE_SEARCH_DIR=C:\Users\shubh\.face_search
   PHOTO_ROOT=C:\Users\shubh\OneDrive\Desktop\Projects\ceremony1\images
   INSIGHTFACE_DIR=C:\Users\shubh\.insightface
   ```
3. Start both services with:
   ```bash
   docker compose up --build
   ```
4. Open `http://127.0.0.1:8511` in your browser.

---

## 4. Private AWS S3 Image Browsing & Storage

The backend includes native private S3 integration for photo libraries hosted in AWS:

1. Create a private S3 bucket with **Block Public Access** enabled.
2. Grant IAM policy `s3:ListBucket` and `s3:GetObject` to the backend IAM user or role.
3. Configure the backend environment variables:
   ```env
   AWS_REGION=eu-north-1
   S3_BUCKET=your-bucket-name
   S3_PREFIX=images/
   S3_BROWSE_ENABLED=true
   ```
4. The frontend will automatically show the **Browse S3 bucket** button in the gallery. Credentials never touch the frontend.

---

## 5. GitHub Push Preparation Checklist

Before pushing to GitHub:

- [x] **Separated Folders**: Top-level `frontend/` and `backend/` directories.
- [x] **No Secrets Committed**: `.env` and `.env.*` are in `.gitignore`.
- [x] **No Heavy Files Committed**: Large photo directories (`/images/`), ONNX models (`*.onnx`), and SQLite databases (`*.sqlite3`) are in `.gitignore`.
- [x] **No Build Artifacts**: `node_modules/`, `dist/`, `.venv/`, `__pycache__/` are ignored.
- [x] **Clean Test Suite**: All tests in `tests/` pass (`python -m pytest`).
- [x] **Dockerfiles Updated**: `frontend/Dockerfile` and `backend/Dockerfile` build independently with local contexts.


//infooo
free-tier RAM limits:

Run this single command in your terminal:
powershell
.\.venv\Scripts\python -c "import sys; sys.path.insert(0, 'backend'); from storage import create_s3_storage; from service import index_s3; index_s3(create_s3_storage(), 'backend/index.sqlite3')"
Git push the updated backend/index.sqlite3:
powershell
git commit -am "update index"; git push
Render updates with the new index automatically in under a minute.