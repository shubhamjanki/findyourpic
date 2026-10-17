# Find Your Pic — Developer Guidelines & Onboarding Guide

Welcome to **Find Your Pic**! This guide is written for new developers joining the project. It explains how the application works, how to set it up locally, how face recognition and indexing operate, and how the hybrid local-cloud architecture is structured.

---

## 1. What Find Your Pic Does

Find Your Pic is a private, AI-powered photo search and face clustering web application:
- **Face Clustering**: Automatically detects all faces across thousands of event/ceremony photos and clusters them by person (e.g., `Person 1`, `Person 2`).
- **Face Search**: Upload any selfie or reference photo, and the AI finds all photos in your collection that contain that person.
- **Photo Storage**: Photos live securely in an AWS S3 bucket.
- **Fast Gallery**: Dynamic, in-memory cached 500px thumbnails load the photo grid at 99% reduced bandwidth.

---

## 2. System Architecture

```
┌────────────────────────────────────────────────────────┐
│             Frontend (Vite + React)                    │
│   • Hosted on Vercel (https://findyourpic.vercel.app)  │
│   • Responsive gallery, face tagging, search modal     │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS / REST API
                            ▼
┌────────────────────────────────────────────────────────┐
│             Backend (FastAPI + Python 3.11)            │
│   • Hosted on Render (https://findyourpic-1.onrender.com)
│   • InsightFace ArcFace (512-dim facial vectors)      │
│   • SQLite with Cosine Similarity vector search        │
│   • Dynamic thumbnail generation & LRU caching         │
└──────────────┬──────────────────────────┬──────────────┘
               │                          │
               ▼                          ▼
    ┌──────────────────────┐   ┌──────────────────────┐
    │  SQLite Database     │   │   AWS S3 Storage     │
    │  (/data/index.sqlite3│   │   (Bucket: images/)  │
    │  Stores 512-dim      │   │   Original high-res  │
    │  face embeddings     │   │   ceremony photos    │
    └──────────────────────┘   └──────────────────────┘
```

---

## 3. Prerequisites

Before starting, make sure you have:
1. **Python 3.10+** (Python 3.11 recommended)
2. **Node.js 18+** & **npm**
3. **Git**
4. **AWS IAM Credentials** with `s3:GetObject` and `s3:ListBucket` permissions for the photo bucket.

---

## 4. Local Development Setup

### Step 1: Clone the Repository
```bash
git clone https://github.com/shubhamjanki/findyourpic.git
cd findyourpic
```

### Step 2: Set Up Python Backend Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r backend/requirements.txt
```

### Step 3: Configure Environment Variables (`.env`)
Create a `.env` file in the project root (or copy from `.env.example`):

```ini
# Storage backend: "s3" or "local"
STORAGE_BACKEND=s3

# AWS / S3 Configuration
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_REGION=eu-north-1
S3_BUCKET=find-your-pic-photos-2026-unique
S3_PREFIX=images/
S3_BROWSE_ENABLED=true

# Local SQLite Database Path
FACE_SEARCH_DB=backend/index.sqlite3

# InsightFace Model (buffalo_s is lightweight and recommended)
INSIGHTFACE_MODEL=buffalo_s

# Allowed CORS Origins for Frontend
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://findyourpic.vercel.app

# Backend URL used by frontend
VITE_API_BASE=http://localhost:8000
```

### Step 4: Install Frontend Dependencies
```bash
cd frontend
npm install
cd ..
```

---

## 5. Running the Application Locally

### Start Backend
In terminal 1 (with `.venv` activated):
```bash
cd backend
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```
- API will be live at: `http://localhost:8000`
- Health check: `http://localhost:8000/api/health`
- Config & stats: `http://localhost:8000/api/config`

### Start Frontend
In terminal 2:
```bash
cd frontend
npm run dev
```
- Web UI will be live at: `http://localhost:5173`

---

## 6. How Face Recognition & Indexing Work

### 1. Face Detection & Feature Extraction
1. **Model**: Uses InsightFace `buffalo_s` pack:
   - **SCRFD (`det_500m.onnx`)**: Detects face locations and facial landmarks.
   - **ArcFace (`w600k_mbf.onnx`)**: Extracts a **512-dimensional normalized vector** for each face (`shape: (512,) float32`).
2. **Storage in SQLite**:
   - `files` table: Stores photo path, size, mtime, and S3 ETag (MD5).
   - `faces` table: Stores `path`, `face_index`, bounding box `(x1, y1, x2, y2)`, and raw `embedding` (2,048 bytes).

### 2. Search & Clustering
- Two faces belong to the same person if the **cosine similarity** between their embeddings is $\ge 0.48$ (`PERSON_MATCH_THRESHOLD`).
- When a user uploads a photo to search, the backend runs detection on that single image, computes dot products against all vectors in SQLite, and returns sorted matches in milliseconds.

---

## 7. The Hybrid Workflow: Local Indexing + Cloud Search

### Why do we index locally?
High-resolution DSLR ceremony photos can be 18 to 45 Megapixels (3–10 MB each). Decoding and running face detection on hundreds of high-res photos in bulk requires 1–2 GB of peak RAM. 

Free-tier cloud hosts (like Render) have a **512 MB RAM limit**, which will trigger an Out Of Memory (OOM) kill during heavy bulk indexing.

### The Solution:
1. **Bulk Indexing is done on your local computer** (no memory constraints).
2. **The resulting SQLite index (`backend/index.sqlite3`) is committed to git**.
3. **The Docker image on Render pre-seeds `/data/index.sqlite3` with this index**.
4. **Cloud backend only handles search queries**: Querying 1 face uses only ~30 MB RAM and executes in ~150 ms without any risk of OOM.

### How to Index New Photos from S3:
Whenever new photos are uploaded to S3:
```powershell
# 1. Run local indexing (processes only new photos, skips existing ones)
.\.venv\Scripts\python -c "import sys; sys.path.insert(0, 'backend'); from storage import create_s3_storage; from service import index_s3; index_s3(create_s3_storage(), 'backend/index.sqlite3')"

# 2. Push the updated index to GitHub
git add backend/index.sqlite3
git commit -m "update index"
git push
```
Render automatically rebuilds and deploys the updated index in under 1 minute.

---

## 8. Dynamic Thumbnails & Performance

To prevent downloading multi-megabyte photos when loading the gallery:
- **Photo Gallery Grid**: Requests `/api/s3/image?key=...&thumb=true`.
  - Backend resizes image to **500px JPEG** on the fly.
  - Reduces file size by **99.2%** (e.g., from **3.2 MB down to 25 KB**).
  - Uses an in-memory **LRU cache** (`_thumb_cache`) so repeated views load in milliseconds.
- **Faces / People View**: Requests `/api/face?path=...&x1=...`.
  - Dynamically crops the face box with margin padding into a **320px JPEG (~15 KB)**.
- **Full Resolution Viewer**: Full original resolution is only served when a user opens the modal viewer or downloads the photo.

---

## 9. Key API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check (`{"status": "ok"}`) |
| `/api/config` | GET | Current collection name, storage type, indexed photo & face counts |
| `/api/photos` | GET | Paginated list of all indexed photos |
| `/api/people` | GET | Clustered groups of recognized people with face preview crops |
| `/api/face` | GET | Returns dynamic cropped 320px JPEG thumbnail for a face |
| `/api/s3/image` | GET | Returns photo bytes (`?thumb=true` for 500px preview, `?thumb=false` for raw) |
| `/api/detect` | POST | Accepts user-uploaded photo, returns detected face boxes and token |
| `/api/search` | POST | Takes face token, returns matching photos sorted by similarity |
| `/api/download-zip` | POST | Generates and downloads a ZIP of selected photos |

---

## 10. Production Deployment Guide

### Backend on Render
1. Create a **Web Service** connected to your GitHub repository.
2. Build Command: Uses `backend/Dockerfile`.
3. Root Directory: `backend` (or root with Docker context set to `.`).
4. Set **Environment Variables** in Render Dashboard:
   - `AWS_ACCESS_KEY_ID`: `your_key`
   - `AWS_SECRET_ACCESS_KEY`: `your_secret`
   - `AWS_REGION`: `eu-north-1`
   - `S3_BUCKET`: `find-your-pic-photos-2026-unique`
   - `S3_PREFIX`: `images/`
   - `STORAGE_BACKEND`: `s3`
   - `S3_BROWSE_ENABLED`: `true`
   - `CORS_ORIGINS`: `https://findyourpic.vercel.app`

### Frontend on Vercel
1. Import repository on **Vercel**.
2. Framework Preset: **Vite**.
3. Root Directory: `frontend`.
4. Environment Variables:
   - `VITE_API_BASE`: `https://findyourpic-1.onrender.com`

---

## 11. Troubleshooting Checklist

| Issue | Likely Cause | Solution |
|---|---|---|
| **CORS Policy Error** in browser console | Backend missing frontend origin in `CORSMiddleware` | Check `backend/app.py` has `https://findyourpic.vercel.app` and `allow_methods=["*"]`. |
| **502 Bad Gateway on `/api/face` or `/api/s3/image`** | AWS credentials missing in Render dashboard, causing Boto3 to hang | Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in Render Web Service -> Environment. |
| **Frontend shows "No faces indexed yet"** | `backend/index.sqlite3` was not committed or Render database is empty | Run local indexing command and git push `backend/index.sqlite3`. |
| **Docker Build OOM during buffalo_s download** | Slow network or model extraction failed | `Dockerfile` includes fallback; model downloads into `/root/.insightface/models/buffalo_s`. |

---

*Happy Coding! For any questions, refer to `architecture.md` or open an issue.*
