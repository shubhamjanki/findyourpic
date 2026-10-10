"""Process configuration. Database and photo root come from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from face_search.index import index_stats
from face_search.utils import DEFAULT_DATABASE


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


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
        origins = cors_origins or _split_origins(
            os.environ.get(
                "CORS_ORIGINS",
                "http://127.0.0.1:8511,http://localhost:8511,http://127.0.0.1:5173,http://localhost:5173",
            )
        )
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
