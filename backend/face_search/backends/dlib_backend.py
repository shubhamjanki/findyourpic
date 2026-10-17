"""Optional face_recognition/dlib backend."""

from __future__ import annotations

import numpy as np

from face_search.backends.base import BoundingBox, FaceBackend, FaceDetection


class DlibBackend(FaceBackend):
    def __init__(self, model: str = "hog") -> None:
        try:
            import face_recognition
        except ImportError as exc:
            raise RuntimeError(
                "The dlib backend needs face_recognition and dlib. "
                "Install with: pip install -e '.[dlib]'"
            ) from exc
        self._face_recognition = face_recognition
        self._model = model

    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        locations = self._face_recognition.face_locations(image, model=self._model)
        return [
            FaceDetection(
                BoundingBox(left, top, right, bottom),
                payload=(top, right, bottom, left),
            )
            for top, right, bottom, left in locations
        ]

    def embed(self, image: np.ndarray, face: FaceDetection) -> np.ndarray:
        encodings = self._face_recognition.face_encodings(
            image,
            known_face_locations=[face.payload],
            num_jitters=1,
        )
        if not encodings:
            raise RuntimeError("dlib could not encode the detected face.")
        return np.asarray(encodings[0], dtype=np.float32).reshape(-1)