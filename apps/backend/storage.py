"""Photo object access. Local volumes in Docker Compose; cloud adapters are stubs."""

from __future__ import annotations

import os
from pathlib import Path

from face_search.utils import SUPPORTED_EXTENSIONS


class StorageError(RuntimeError):
    """Raised when a storage backend is missing configuration or cannot serve an object."""


class LocalStorage:
    """Read originals from a bind-mounted photo folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def resolve(self, requested: str) -> Path:
        if not requested:
            raise StorageError("Missing image path.")
        requested_path = Path(requested).expanduser()
        image_path = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (self.root / requested_path).resolve()
        )
        try:
            image_path.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("Image is outside the indexed collection.") from exc
        if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS or not image_path.is_file():
            raise FileNotFoundError("Indexed image is missing or unsupported.")
        return image_path

    def relative_name(self, image_path: Path) -> str:
        return image_path.relative_to(self.root).as_posix()


class R2Storage:
    """Placeholder for Cloudflare R2 originals and thumbnails."""

    def __init__(self) -> None:
        account = os.environ.get("R2_ACCOUNT_ID", "")
        bucket = os.environ.get("R2_BUCKET", "")
        if not account or not bucket:
            raise StorageError(
                "Cloudflare R2 is not configured. Use STORAGE_BACKEND=local for Docker Compose, "
                "or set R2_ACCOUNT_ID and R2_BUCKET later."
            )
        raise StorageError("Cloudflare R2 storage is a stub in this release.")


class SupabaseStorage:
    """Placeholder for Supabase Storage originals and thumbnails."""

    def __init__(self) -> None:
        url = os.environ.get("SUPABASE_URL", "")
        if not url:
            raise StorageError(
                "Supabase storage is not configured. Use STORAGE_BACKEND=local for Docker Compose, "
                "or set SUPABASE_URL later."
            )
        raise StorageError("Supabase storage is a stub in this release.")


def create_storage(root: Path, backend: str = "local") -> LocalStorage:
    if backend == "r2":
        return R2Storage()
    if backend == "supabase":
        return SupabaseStorage()
    if backend != "local":
        raise StorageError(f"Unknown storage backend: {backend}")
    return LocalStorage(root)
