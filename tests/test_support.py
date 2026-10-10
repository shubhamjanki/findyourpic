from __future__ import annotations

import numpy as np

from face_search.backends.base import BoundingBox, FaceBackend, FaceDetection


class FakeBackend(FaceBackend):
    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        if int(image.mean()) < 5:
            return []
        height, width, _ = image.shape
        return [FaceDetection(BoundingBox(1, 2, max(2, width - 1), max(3, height - 2)))]

    def embed(self, image: np.ndarray, face: FaceDetection) -> np.ndarray:
        return image.mean(axis=(0, 1)).astype(np.float32)


class MultiFaceBackend(FakeBackend):
    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        height, width, _ = image.shape
        return [
            FaceDetection(BoundingBox(0, 0, width // 2, height)),
            FaceDetection(BoundingBox(width // 2, 0, width, height)),
        ]

    def embed(self, image: np.ndarray, face: FaceDetection) -> np.ndarray:
        x_start, x_end = (0, image.shape[1] // 2) if face.bbox.x1 == 0 else (image.shape[1] // 2, image.shape[1])
        return image[:, x_start:x_end].mean(axis=(0, 1)).astype(np.float32)