"""Shared backend contract and detection data structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class FaceDetection:
    bbox: BoundingBox
    payload: Any = field(default=None, repr=False, compare=False)


class FaceBackend(ABC):
    """Backend contract. Images are RGB uint8 arrays and boxes use xyxy pixels."""

    @abstractmethod
    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        """Find faces in an RGB image."""

    @abstractmethod
    def embed(self, image: np.ndarray, face: FaceDetection) -> np.ndarray:
        """Return one numeric embedding for a detected face."""