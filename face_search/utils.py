"""Image loading, file discovery, and output helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from face_search.backends.base import BoundingBox

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_DATABASE = Path.home() / ".face_search" / "index.sqlite3"


def iter_image_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        raise NotADirectoryError(f"Image folder does not exist: {root}")
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rgb_image(
    path: Path, max_dimension: int = 1600
) -> tuple[np.ndarray, float, float, tuple[int, int]]:
    """Load EXIF-oriented RGB pixels, downscaled, plus scale and oriented size."""
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive")
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        original_width, original_height = image.size
        if max(image.size) > max_dimension:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail((max_dimension, max_dimension), resampling)
        scale_x = image.width / original_width
        scale_y = image.height / original_height
        return np.asarray(image, dtype=np.uint8), scale_x, scale_y, (original_width, original_height)


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if vector.size == 0 or not np.all(np.isfinite(vector)):
        raise ValueError("Face backend returned an empty or non-finite embedding.")
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        raise ValueError("Face backend returned a zero-length embedding.")
    return vector / norm


def json_matches(matches: Sequence[object]) -> str:
    return json.dumps([asdict(match) for match in matches], indent=2)


def write_csv_matches(matches: Sequence[object], stream: object) -> None:
    writer = csv.DictWriter(
        stream,
        fieldnames=["score", "path", "face_index", "x1", "y1", "x2", "y2"],
    )
    writer.writeheader()
    for match in matches:
        bbox = match.bbox
        writer.writerow(
            {
                "score": f"{match.score:.6f}",
                "path": match.path,
                "face_index": match.face_index,
                "x1": bbox.x1,
                "y1": bbox.y1,
                "x2": bbox.x2,
                "y2": bbox.y2,
            }
        )


def copy_matches(matches: Iterable[object], source_root: Path, destination: Path) -> int:
    copied = 0
    destination.mkdir(parents=True, exist_ok=True)
    for match in matches:
        source = source_root / match.path
        target = destination / match.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def create_contact_sheet(
    matches: Sequence[object], source_root: Path, output_path: Path,
    columns: int = 4, cell_size: tuple[int, int] = (320, 300),
) -> None:
    if columns < 1:
        raise ValueError("columns must be positive")
    if not matches:
        raise ValueError("Cannot create a contact sheet with no matches.")
    rows = (len(matches) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_size[0], rows * cell_size[1]), "white")
    draw = ImageDraw.Draw(sheet)
    for index, match in enumerate(matches):
        source_path = source_root / match.path
        try:
            with Image.open(source_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        except Exception as exc:
            LOGGER.warning("Skipping contact-sheet image %s: %s", source_path, exc)
            continue
        original_width, original_height = image.size
        image.thumbnail((cell_size[0] - 12, cell_size[1] - 48))
        x = (index % columns) * cell_size[0] + (cell_size[0] - image.width) // 2
        y = (index // columns) * cell_size[1] + 6
        sheet.paste(image, (x, y))
        bbox: BoundingBox = match.bbox
        box = (
            x + round(bbox.x1 * image.width / original_width),
            y + round(bbox.y1 * image.height / original_height),
            x + round(bbox.x2 * image.width / original_width),
            y + round(bbox.y2 * image.height / original_height),
        )
        draw.rectangle(box, outline=(220, 35, 35), width=3)
        label = f"{match.score:.3f}  {match.path}"
        draw.text(((index % columns) * cell_size[0] + 6, y + image.height + 8), label, fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)