# Architecture and deployment

## Current application

Find Your Pic indexes an authorized photo folder, stores file metadata and face embeddings in SQLite, and searches that index using a face detected in a reference photo. The UI runs in Docker Compose with a React (Vite) frontend and a FastAPI backend, split so the browser talks to exactly one local URL.

```text
Browser
  └── http://127.0.0.1:8511 (Compose publishes loopback only; no auth in v1)
        │
        ▼
apps_web_nginx            serves the React build; proxies /api/* to the API
        │
        ▼
apps_api_FastAPI           /api/config /api/photos /api/people /api/image
  ├── face_search.index    /api/face /api/detect /api/search /api/person-name
  ├── face_search.search   /api/feedback /api/download-zip /api/delete-photo
  ├── InsightFace (or dlib)/api/collection-name /api/index /api/clear
  ├── SQLite               ~/.face_search/index.sqlite3 → /data
  ├── photos               PHOTO_ROOT → /photos
  └── models               ~/.insightface → /root/.insightface (read-only)
```

`python -m face_search.web` is the no-Docker fallback: it runs the same FastAPI app on localhost and serves `apps/frontend/dist` (or the legacy static UI) when present. The Root README documents `docker compose up --build`, the environment variables below, and the loopback-only warning.

### Container layout

- `api` (`apps/backend/Dockerfile`, Uvicorn on `0.0.0.0:8000`) — host map is `127.0.0.1:8000:8000`; the web container proxies to it so the browser never calls the API port directly.
- `web` (`apps/frontend/Dockerfile`, nginx serving the Vite build) — host map is `127.0.0.1:8511:80`; nginx reverse-proxies `/api/*` to `api:8000`. The frontend uses relative `/api/...` requests locally; `VITE_API_BASE` supports a separately hosted frontend.
- Both services bind to loopback only. There is no authentication in this version; changing the published bindings to `0.0.0.0` would expose unauthenticated photo access.

### Environment and storage

Database and photo root come from the environment, never from client `db=` / `collection` query parameters (matching the rule that hosted clients must not pick filesystem paths):

| Variable | Host path (via `.env`) | Container |
| --- | --- | --- |
| `FACE_SEARCH_DIR` | folder containing `index.sqlite3` | `/data` → `FACE_SEARCH_DB=/data/index.sqlite3` |
| `PHOTO_ROOT` | indexed photo collection | `/photos` |
| `INSIGHTFACE_DIR` | local `~/.insightface` model pack | `/root/.insightface`, read-only |

The existing corpus is imported, not re-embedded: the SQLite database with its ~4,500 face embeddings, the photo folder, and the model weights are bind-mounted read-only-ish into the API container, so startup does not rescan photos or regenerate vectors. Local indexing and normal photo browsing continue to use `PHOTO_ROOT`. An opt-in S3 browser can list and serve supported image objects through the API when `S3_BROWSE_ENABLED=true`; it is read-only, does not add images to the local index, and must only be enabled on a trusted local instance because the API has no authentication. `R2Storage`/`SupabaseStorage` remain stubs. CORS is restricted to the local web origin (`http://127.0.0.1:8511`), and `/api/health` powers the compose healthcheck. Indexing stays available on demand via `docker compose exec api python -m face_search.cli index ...` or the Collection Update control (which uses the configured photo root).

### Current limitations in this layout

- Single-user, localhost-only, no authentication, no multi-host separation.
- Indexing runs synchronously; a fixed-corpus search deployment can skip it after the one-time import.
- Uploaded query embeddings are held in process memory, not shared across instances.
- The index, metadata, and vectors remain local (SQLite + `faces.embedding` cosine scan); the S3 browser is a read-only viewer, not a photo-storage migration or hosted multi-user adapter.

## Search-only deployment: reuse the existing embeddings

The photo collection is fixed and already indexed. **The corpus does not need to be re-embedded for every deployment or search.** Copy/import the existing database's face rows and vectors once, retaining the bounding boxes and backend identity. On each image search, the API only needs to:

1. Detect and embed the face in the uploaded reference image.
2. Compare that one query vector with the already-stored collection vectors.
3. Return matching photo keys, scores, and bounding boxes.

At the current scale of roughly 4,500 indexed faces, a regular cosine scan should be a reasonable first version. Measure before adding pgvector or another vector engine. The query backend/model must be compatible with the index's backend/model; changing the index backend would require rebuilding the corpus embeddings.

### Suggested cloud layout

```text
Browser
  ├── Cloudflare Pages: static frontend
  ├── Cloudflare R2 private bucket: original photos + display thumbnails
  └── Render: Python search API (CPU; choose an instance with enough memory)
        ├── InsightFace model: query photos only
        ├── one-time imported face vectors and metadata
        └── authenticated matching photo object keys

Supabase Postgres (or another durable database)
  └── imported face vectors, boxes, photo keys, labels, feedback
```

Suggested responsibilities:

- **Frontend — Cloudflare Pages:** serves the static interface. It does not run face recognition or need access to the user's computer. Add a configurable API base URL; current requests are relative same-origin paths.
- **Photo storage — Cloudflare R2:** upload the existing collection once and store opaque object keys in the index. Keep the bucket private. Use the API to authorize image requests or issue short-lived signed URLs. Generate small display thumbnails once from the same originals for the All/Collection grids; this is image resizing, not face-embedding generation. Load originals for the viewer.
- **Existing index — durable DB:** migrate the existing SQLite face/file rows once to a persistent database. Supabase Postgres is one possible hosted option; pgvector is optional at this corpus size. Metadata edits (person names, collection title, feedback) also need durable storage. Keep a source backup until the migrated index has been verified.
- **API/inference — Render:** run the Python API on CPU for query-face detection and embedding, then search the stored vectors. The container binds to Render's `PORT`; model weights must be supplied before startup and loaded once per service process. Render's free service has limited memory and no persistent disk, so a paid instance and disk may be required. A free GPU is not assumed.

The API still does inference for each uploaded reference photo, but it does **not** recompute embeddings for the whole library. Do not expose the current local API publicly without the changes below.

### Storage reality check

Cloudflare R2 currently lists a free allowance of 10 GB-month of Standard storage, monthly request allowances, and free internet egress; verify the live quota before deployment: [R2 pricing](https://developers.cloudflare.com/r2/pricing/). Five sampled originals averaged about 8.3 MB. If that sample is representative, 1,173 originals would occupy about 9.7 GB before thumbnails or growth. This is only an estimate; measure the full library. If it exceeds the free allowance, either pay for storage or decide whether compressed/resized copies are acceptable in place of keeping every original in cloud storage.

No free-tier provider guarantees that this entire stack will remain free. Render instance and persistent-disk charges, cold-start time, outbound image delivery, storage, database quotas, and model/container size all matter. Set budget alerts and check each provider's current terms. Render pricing: [Render pricing](https://render.com/pricing). Cloudflare's static Pages limits: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/).

## Changes required before splitting hosts

The current application is a trusted local app, not a hosted multi-user service:

- The frontend supports `VITE_API_BASE`, and the API has a configurable CORS allowlist; split deployments must configure the exact frontend origin.
- The Docker API uses Uvicorn and listens on the container interface; Compose publishes it to loopback only for local use.
- It has no authentication, user separation, or authorization. API clients can supply database and collection paths.
- Image endpoints read local files; hosted storage must use authorized object keys instead.
- Uploaded query embeddings are held in process memory, not shared across instances.
- Indexing runs synchronously, although a fixed-corpus search deployment can disable indexing after its one-time import.

Before internet deployment:

1. Configure `VITE_API_BASE` and `CORS_ORIGINS` for the deployed frontend and API origins.
2. Add authentication and authorization. Derive the user's index and object prefix from their authenticated identity; never trust a submitted filesystem/database path.
3. Import existing embeddings once and verify counts, dimensions, backend, sample searches, and photo-to-key mappings.
4. Move photos to private object storage; implement authorized thumbnails/original retrieval.
5. Use a production server with health checks, platform port binding, request-size limits, rate limits, and safe logs.
6. Keep the model initialized once per API process. Store feedback/labels and migrated vectors durably.
7. Back up and test restore of database and photo objects before retiring the local source.

## Optional later scaling

- Add an indexing job only if new or changed photos are later added. Initial migration and embedding creation can be performed offline once.
- Add a background queue only if query processing or future indexing needs exceed synchronous request limits.
- Add pgvector/ANN only after measured vector-search latency warrants it.
- Use thumbnails for grids and fetch originals only for the selected viewer image.

## Deployment checklist

- [ ] Choose private single-user, household, or multi-user access; implement authentication before remote personal-photo access.
- [ ] Keep images, databases, and model weights out of Git.
- [ ] Measure collection storage size; verify free-tier limits and configure budget alerts.
- [ ] Migrate and verify the existing face vectors once; do not regenerate the corpus embeddings unnecessarily.
- [ ] Confirm query model/backend compatibility with the existing index.
- [ ] Keep photo objects private and test signed/authenticated image access.
- [ ] Test HTTPS, query-photo detection, search latency including cold starts, result thumbnails, labels, feedback, and restore.
- [ ] Review face matches manually; embeddings and similarity scores are not proof of identity.

## Licensing

No repository license is selected. Choose one before accepting outside contributions or granting reuse permissions. Check dependency and model-pack licenses separately; a project license does not grant rights to photos or model weights.
