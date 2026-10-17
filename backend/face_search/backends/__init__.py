"""Face detection and embedding backends."""

from face_search.backends.base import FaceBackend, FaceDetection


def create_backend(name: str) -> FaceBackend:
    """Create a backend lazily so optional frameworks are not imported needlessly."""
    normalized_name = name.lower()
    if normalized_name == "insightface":
        from face_search.backends.insightface_backend import InsightFaceBackend

        return InsightFaceBackend()
    if normalized_name == "dlib":
        from face_search.backends.dlib_backend import DlibBackend

        return DlibBackend()
    raise ValueError(f"Unsupported face backend: {name}")


__all__ = ["FaceBackend", "FaceDetection", "create_backend"]