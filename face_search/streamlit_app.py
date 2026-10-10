"""Optional local Streamlit interface. Run with: streamlit run face_search/streamlit_app.py"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import streamlit as st

from face_search.backends import create_backend
from face_search.index import index_directory, index_stats
from face_search.search import average_embeddings, query_face_embeddings, search_index
from face_search.utils import DEFAULT_DATABASE

LOGGER = logging.getLogger(__name__)


def default_collection(database: Path) -> Path:
    stats = index_stats(database)
    indexed_root = stats.get("root")
    if isinstance(indexed_root, str) and indexed_root:
        return Path(indexed_root)
    local_images = Path.cwd() / "images"
    if local_images.is_dir():
        return local_images
    return Path.home() / "Pictures"


def resolve_match_path(database: Path, collection: Path, relative_path: str) -> Path:
    stored_path = Path(relative_path)
    if stored_path.is_absolute() and stored_path.exists():
        return stored_path
    image_path = collection / relative_path
    if image_path.exists():
        return image_path
    indexed_root = index_stats(database).get("root")
    if isinstance(indexed_root, str) and indexed_root:
        indexed_path = Path(indexed_root) / relative_path
        if indexed_path.exists():
            return indexed_path
    return image_path


def search_reference(
    query_bytes: bytes,
    suffix: str,
    database: Path,
    collection: Path,
    backend_name: str,
    threshold: float,
    top_n: int,
) -> None:
    temporary_path: Path | None = None
    try:
        backend = create_backend(backend_name)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_file.write(query_bytes)
            temporary_path = Path(temporary_file.name)
        faces = query_face_embeddings(temporary_path, backend)
        if len(faces) > 1:
            chosen = st.selectbox("Choose query face", range(len(faces)))
            embedding = faces[chosen][1]
        else:
            embedding = faces[0][1]
        results = search_index(average_embeddings([embedding]), database, threshold, top_n)
        st.caption(f"{len(results)} matching image(s)")
        grid = st.columns(4)
        for index, match in enumerate(results):
            image_path = resolve_match_path(database, collection, match.path)
            with grid[index % len(grid)]:
                if image_path.exists():
                    st.image(str(image_path), width="stretch")
                    st.caption(f"{match.score:.3f}  {match.path}")
                else:
                    st.warning(f"Missing image: {match.path}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


st.set_page_config(page_title="Local Face Search", layout="wide")
st.title("Local Face Search")
database = Path(st.text_input("Index database", value=str(DEFAULT_DATABASE))).expanduser()
collection = Path(st.text_input("Image folder", value=str(default_collection(database)))).expanduser()
backend_name = st.selectbox("Backend", ("insightface", "dlib"))

if st.button("Index or update folder", type="primary"):
    try:
        report = index_directory(collection, database, backend_name)
        st.success(
            f"Indexed {report.indexed} files and {report.face_count} faces; "
            f"skipped {report.skipped}, removed {report.removed}, failed {report.failed}."
        )
        for error in report.errors:
            st.warning(error)
    except Exception as exc:
        LOGGER.exception("Indexing failed")
        st.error(str(exc))

threshold = st.slider("Minimum cosine similarity", min_value=0.0, max_value=1.0, value=0.45, step=0.01)
top_n = st.number_input("Maximum results", min_value=1, max_value=1000, value=50)

upload_tab, camera_tab = st.tabs(("Upload", "Camera"))

with upload_tab:
    query_file = st.file_uploader("Query face photo", type=("jpg", "jpeg", "png", "webp", "bmp"))
    if query_file is not None and st.button("Search upload"):
        try:
            suffix = Path(query_file.name).suffix or ".jpg"
            search_reference(
                query_file.getvalue(),
                suffix,
                database,
                collection,
                backend_name,
                threshold,
                int(top_n),
            )
        except Exception as exc:
            LOGGER.exception("Search failed")
            st.error(str(exc))

with camera_tab:
    camera_file = st.camera_input("Camera face photo")
    if camera_file is not None and st.button("Search camera"):
        try:
            search_reference(
                camera_file.getvalue(),
                ".jpg",
                database,
                collection,
                backend_name,
                threshold,
                int(top_n),
            )
        except Exception as exc:
            LOGGER.exception("Camera search failed")
            st.error(str(exc))
