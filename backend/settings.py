"""Process configuration. Database and photo root come from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from face_search.index import index_stats
from face_search.utils import DEFAULT_DATABASE


def _load_dotenv_if_present() -> None:
    """Load a .env file from the project root or backend directory if present.

    This enables local dev runs without Docker to pick up PHOTO_ROOT,
    FACE_SEARCH_DB, etc. without needing to export them manually.
    Variables already set in the environment take precedence.
    """
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            with env_path.open(encoding="utf-8") as file_:
                for line in file_:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key and key not in os.environ:
                        os.environ[key] = value
            break


_load_dotenv_if_present()


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


_DEFAULT_CORS = (
    "http://127.0.0.1:8511,"
    "http://localhost:8511,"
    "http://127.0.0.1:5173,"
    "http://localhost:5173,"
    "https://findyourpic.vercel.app"
)


@dataclass(frozen=True)
class Settings:
    database: Path
    photo_root: Path | None
    storage_backend: str
    cors_origins: list[str]

    @classmethod
    def load(
        cls,
        *,
        database: Path | None = None,
        photo_root: Path | None = None,
        cors_origins: list[str] | None = None,
        storage_backend: str | None = None,
    ) -> Settings:
        db = Path(database or os.environ.get("FACE_SEARCH_DB", str(DEFAULT_DATABASE))).expanduser()
        env_root = os.environ.get("PHOTO_ROOT")
        root = photo_root if photo_root is not None else (Path(env_root).expanduser() if env_root else None)
        raw_origins = os.environ.get("CORS_ORIGINS", _DEFAULT_CORS)
        origins = cors_origins or _split_origins(raw_origins)
        backend = (storage_backend or os.environ.get("STORAGE_BACKEND", "local")).strip().lower()
        return cls(database=db, photo_root=root, storage_backend=backend, cors_origins=origins)

    def collection_root(self) -> Path:
        if self.photo_root is not None:
            return self.photo_root.expanduser().resolve()
        indexed = index_stats(self.database).get("root")
        if isinstance(indexed, str) and indexed:
            return Path(indexed).expanduser().resolve()
        local_images = Path.cwd() / "images"
        if local_images.is_dir():
            return local_images.resolve()
        return (Path.home() / "Pictures").resolve()
