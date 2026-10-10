# Contributing

Thanks for helping improve Find Your Pic. Contributions should preserve the project's local-first behavior, privacy expectations, and support for authorized personal photo organization.

## Before opening a pull request

1. Open an issue to discuss substantial features or behavior changes.
2. Keep changes focused and follow the existing Python and browser JavaScript patterns.
3. Do not add real personal photos, face embeddings, model weights, databases, API keys, or other private data to a commit.
4. Use synthetic images and deterministic fake backends in tests.
5. Update user-facing documentation when commands, installation, behavior, or limitations change.

## Development setup

Use Python 3.11 or another supported Python version, then install the project and test dependencies:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The browser UI is a React (Vite) app in `apps/frontend` and the FastAPI backend is in `apps/backend`. For a no-Docker local smoke test, run:

```powershell
python -m face_search.web --port 8511
```

To rebuild the frontend during development, install and run Vite inside `apps/frontend` (the API must already be running):

```powershell
cd apps/frontend
npm install
npm run dev
```

## Checks

Run the tests:

```powershell
python -m pytest
```

Check frontend JavaScript syntax when changing the browser UI:

```powershell
node --check face_search/web/app.js
```

Add or update focused tests for behavior changes. Tests should not download model weights, require a GPU, or need a personal image collection.

## Pull request notes

Include a concise summary, the reason for the change, how it was tested, and any privacy or migration impact. Do not claim face-match accuracy as identity certainty. Reviewers should be able to run the documented checks without private data.
