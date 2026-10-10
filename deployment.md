# Free Tier Deployment Guide (~200 Users)

Deploying an application with Machine Learning (InsightFace) and image hosting for free requires piecing together services that offer generous free tiers. Traditional free servers (like Render or Railway) only offer 512MB RAM, which will crash when loading facial recognition models. Additionally, local SQLite databases are wiped daily on free "serverless" platforms.

Because the codebase already has stubs for **Supabase** and **Cloudflare R2** in `apps/backend/storage.py`, you can easily transition to this 100% free stack capable of handling ~200 users.

## The Recommended Free Stack

### 1. Frontend (React/Vite) ➔ Vercel or Cloudflare Pages
*   **Cost:** 100% Free.
*   **Why:** They specialize in static React apps. You connect your GitHub repository, set the build folder to `apps/frontend`, and it deploys your UI globally on a blazing-fast CDN. 
*   **Setup:** Set your `VITE_API_BASE` environment variable here to point to your new backend URL.

### 2. Database ➔ Supabase
*   **Cost:** 100% Free tier.
*   **Why:** Cloud platforms wipe local files (like `index.sqlite3`) when servers sleep. Supabase gives you a permanent, free PostgreSQL database in the cloud.
*   **Setup:** Implement the `SupabaseStorage` stub in `storage.py` and point your backend to the Supabase connection URL.

### 3. Photo Storage ➔ Cloudflare R2
*   **Cost:** Free tier includes 10GB of storage and millions of reads per month.
*   **Why:** You cannot store thousands of user photos directly on a free backend server container. Cloudflare R2 acts exactly like Amazon S3 but is completely free for the first 10GB.
*   **Setup:** Implement the `R2Storage` stub in `storage.py`. When a user uploads a photo, your backend will stream it directly to R2.

### 4. Backend (FastAPI + InsightFace) ➔ Hugging Face Spaces (Docker)
*   **Cost:** 100% Free.
*   **Why:** Hugging Face Spaces offers a **free Docker Space tier with 2 vCPUs and 16GB of RAM** because it is specifically designed to host AI models. This avoids the 512MB out-of-memory crashes you'd get on other free platforms.
*   **Setup:** Upload your `apps/backend/Dockerfile` to a new Docker Space on Hugging Face. It will run your FastAPI server with plenty of RAM for the facial embeddings.

---

## Migration Steps

To transition your local Docker Compose setup to this cloud stack:

1. **Migrate Data:** Move your local `index.sqlite3` records to your new **Supabase** database.
2. **Migrate Photos:** Upload your existing `PHOTO_ROOT` folders to a new **Cloudflare R2** bucket.
3. **Deploy Backend:** Deploy the `apps/backend/Dockerfile` to a **Hugging Face Space**. Configure it with your Supabase and R2 credentials as environment variables.
4. **Deploy Frontend:** Deploy `apps/frontend` to **Vercel**, and set the `VITE_API_BASE` variable to your Hugging Face Space URL.
