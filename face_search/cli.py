"""Command-line interface for local face search."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from face_search.backends import create_backend
from face_search.backends.base import FaceBackend
from face_search.index import clear_index, index_directory, index_stats
from face_search.search import (
    average_embeddings,
    query_face_embeddings,
    search_index,
)
from face_search.utils import (
    DEFAULT_DATABASE,
    copy_matches,
    create_contact_sheet,
    json_matches,
    write_csv_matches,
)

LOGGER = logging.getLogger("face_search")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="face-search",
        description="Search an authorized local image collection for a face.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Index or update a local image folder")
    index_parser.add_argument("--images", type=Path, required=True)
    index_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    index_parser.add_argument("--backend", choices=("insightface", "dlib"), default="insightface")
    index_parser.add_argument("--workers", type=int, default=1)
    index_parser.add_argument("--max-dimension", type=int, default=1600)

    search_parser = subparsers.add_parser("search", help="Find matching faces")
    search_parser.add_argument("--query", type=Path, action="append", required=True)
    search_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    search_parser.add_argument("--backend", choices=("insightface", "dlib"), default="insightface")
    search_parser.add_argument("--threshold", type=float, default=0.45)
    search_parser.add_argument("--top", type=int, default=50)
    search_parser.add_argument("--format", choices=("table", "json", "csv"), default="table")
    search_parser.add_argument("--copy-to", type=Path)
    search_parser.add_argument("--contact-sheet", type=Path)
    search_parser.add_argument("--max-dimension", type=int, default=1600)

    stats_parser = subparsers.add_parser("stats", help="Show index counts")
    stats_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    clear_parser = subparsers.add_parser("clear", help="Remove all indexed files and faces")
    clear_parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    return parser


def _choose_query_faces(path: Path, backend: FaceBackend, max_dimension: int):
    faces = query_face_embeddings(path, backend, max_dimension)
    if len(faces) == 1:
        return [faces[0][1]]
    LOGGER.info("Detected %d faces in %s", len(faces), path)
    for index, (bbox, _) in enumerate(faces):
        LOGGER.info("  %d: box=(%d, %d, %d, %d)", index, bbox.x1, bbox.y1, bbox.x2, bbox.y2)
    while True:
        sys.stderr.write(f"Choose face index [0-{len(faces) - 1}]: ")
        try:
            selected = int(input())
        except EOFError:
            raise ValueError("Face selection requires an interactive terminal.") from None
        except ValueError:
            LOGGER.warning("Enter a face index number.")
            continue
        if 0 <= selected < len(faces):
            return [faces[selected][1]]
        LOGGER.warning("Face index is out of range.")


def _display_matches(matches: list[object], output_format: str) -> None:
    if output_format == "json":
        sys.stdout.write(json_matches(matches) + "\n")
    elif output_format == "csv":
        write_csv_matches(matches, sys.stdout)
    else:
        sys.stdout.write(f"{'SCORE':>7}  {'PATH':<48}  BOUNDING BOX\n")
        sys.stdout.write("-" * 90 + "\n")
        for match in matches:
            box = match.bbox
            sys.stdout.write(
                f"{match.score:7.4f}  {match.path:<48}  "
                f"({box.x1}, {box.y1}, {box.x2}, {box.y2})\n"
            )
        sys.stdout.write(f"{len(matches)} match(es)\n")


def _run_search(args: argparse.Namespace) -> int:
    backend = create_backend(args.backend)
    selected_embeddings = []
    for query_path in args.query:
        selected_embeddings.extend(
            _choose_query_faces(query_path, backend, args.max_dimension)
        )
    query_embedding = average_embeddings(selected_embeddings)
    matches = search_index(query_embedding, args.db, args.threshold, args.top)
    _display_matches(matches, args.format)
    source_root_value = index_stats(args.db)["root"]
    if source_root_value is None:
        raise FileNotFoundError(f"No source folder is recorded in index: {args.db}")
    source_root = Path(source_root_value)
    if args.copy_to:
        copy_matches(matches, source_root, args.copy_to)
    if args.contact_sheet:
        create_contact_sheet(matches, source_root, args.contact_sheet)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            report = index_directory(
                args.images, args.db, args.backend, workers=args.workers,
                max_dimension=args.max_dimension,
            )
            LOGGER.info(
                "Scanned %d; indexed %d (%d faces); skipped %d; removed %d; failed %d.",
                report.scanned, report.indexed, report.face_count, report.skipped,
                report.removed, report.failed,
            )
            for error in report.errors:
                LOGGER.error("%s", error)
            return 1 if report.failed else 0
        if args.command == "search":
            return _run_search(args)
        if args.command == "stats":
            sys.stdout.write(json.dumps(index_stats(args.db), indent=2) + "\n")
            return 0
        clear_index(args.db)
        LOGGER.info("Cleared face index at %s", args.db)
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())