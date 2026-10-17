from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

from face_search.index import index_directory, index_stats
from test_support import FakeBackend


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 12), color).save(path)


def test_index_incremental_hash_and_deleted_files(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    image_path = collection / "nested" / "person.jpg"
    _write_image(image_path, (255, 0, 0))
    database = tmp_path / "index.sqlite3"

    first = index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    assert (first.indexed, first.face_count, first.failed) == (1, 1, 0)
    second = index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    assert (second.indexed, second.skipped) == (0, 1)

    previous_stat = image_path.stat()
    _write_image(image_path, (0, 255, 0))
    os.utime(image_path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    changed = index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    assert changed.indexed == 1

    image_path.unlink()
    removed = index_directory(collection, database, backend=FakeBackend(), show_progress=False)
    assert removed.removed == 1
    assert index_stats(database)["faces"] == 0


def test_index_reports_checking_and_indexing_progress(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "first.jpg", (255, 0, 0))
    _write_image(collection / "second.jpg", (0, 255, 0))
    progress: list[tuple[str, int, int]] = []

    index_directory(
        collection,
        tmp_path / "index.sqlite3",
        backend=FakeBackend(),
        show_progress=False,
        progress_callback=lambda phase, completed, total: progress.append(
            (phase, completed, total)
        ),
    )

    assert progress[0] == ("checking", 0, 2)
    assert ("checking", 2, 2) in progress
    assert progress[-3:] == [
        ("indexing", 0, 2),
        ("indexing", 1, 2),
        ("indexing", 2, 2),
    ]


def test_corrupt_image_is_reported_without_stopping_index(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    _write_image(collection / "valid.png", (255, 0, 0))
    corrupt = collection / "broken.jpg"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not an image")

    report = index_directory(collection, tmp_path / "index.sqlite3", backend=FakeBackend(), show_progress=False)

    assert report.indexed == 1
    assert report.failed == 1
    assert "broken.jpg" in report.errors[0]


def test_index_can_add_second_folder_incrementally(tmp_path: Path) -> None:
    first_collection = tmp_path / "first"
    second_collection = tmp_path / "second"
    _write_image(first_collection / "person.jpg", (255, 0, 0))
    _write_image(second_collection / "person.jpg", (0, 255, 0))
    database = tmp_path / "index.sqlite3"

    first = index_directory(first_collection, database, backend=FakeBackend(), show_progress=False)
    assert first.indexed == 1

    second = index_directory(second_collection, database, backend=FakeBackend(), show_progress=False)
    assert (second.indexed, second.skipped, second.removed) == (1, 0, 0)
    assert index_stats(database)["files"] == 2

    repeat = index_directory(second_collection, database, backend=FakeBackend(), show_progress=False)
    assert (repeat.indexed, repeat.skipped, repeat.removed) == (0, 1, 0)
    assert index_stats(database)["files"] == 2


def test_index_reports_when_source_root_changes(tmp_path: Path) -> None:
    first_collection = tmp_path / "first"
    second_collection = tmp_path / "second"
    _write_image(first_collection / "person.jpg", (255, 0, 0))
    _write_image(second_collection / "person.jpg", (0, 255, 0))
    database = tmp_path / "index.sqlite3"

    first = index_directory(first_collection, database, backend=FakeBackend(), show_progress=False)
    assert first.source_changed is False

    second = index_directory(second_collection, database, backend=FakeBackend(), show_progress=False)
    assert second.source_changed is True


def test_index_uses_exif_oriented_dimensions_for_boxes(tmp_path: Path) -> None:
    collection = tmp_path / "photos"
    path = collection / "rotated.jpg"
    image = Image.new("RGB", (20, 12), (255, 0, 0))
    exif = image.getexif()
    exif[274] = 6
    path.parent.mkdir(parents=True)
    image.save(path, exif=exif)

    index_directory(collection, tmp_path / "index.sqlite3", backend=FakeBackend(), show_progress=False)
    import sqlite3

    with sqlite3.connect(tmp_path / "index.sqlite3") as connection:
        box = connection.execute("SELECT x1, y1, x2, y2 FROM faces").fetchone()
    assert box is not None
    assert box[2] <= 12
    assert box[3] <= 20
