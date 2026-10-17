"""InsightFace ArcFace backend, preferring GPU inference when available."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from face_search.backends.base import BoundingBox, FaceBackend, FaceDetection


class InsightFaceBackend(FaceBackend):
    def __init__(self, model_name: str = "buffalo_l") -> None:
        model_dir = Path.home() / ".insightface" / "models" / model_name
        if not model_dir.is_dir() or not any(model_dir.glob("*.onnx")):
            raise RuntimeError(
                f"InsightFace model '{model_name}' is not installed locally at "
                f"{model_dir}. Install the model pack before running; this tool "
                "does not download model files."
            )

        try:
            from insightface.app import FaceAnalysis
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "InsightFace is unavailable. Install the project dependencies first."
            ) from exc

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls(directory="")
        available_providers = ort.get_available_providers()
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in available_providers
            else ["CPUExecutionProvider"]
        )
        ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        self._app = FaceAnalysis(
            name=model_name,
            root=str(Path.home() / ".insightface"),
            providers=providers,
        )
        self._app.prepare(ctx_id=ctx_id, det_size=(640, 640))

    def detect(self, image: np.ndarray) -> list[FaceDetection]:
        detected_faces = self._app.get(np.ascontiguousarray(image[:, :, ::-1]))
        results: list[FaceDetection] = []
        for face in detected_faces:
            x1, y1, x2, y2 = np.rint(face.bbox).astype(int).tolist()
            results.append(
                FaceDetection(BoundingBox(x1, y1, x2, y2), payload=face)
            )
        return results

    def embed(self, image: np.ndarray, face: FaceDetection) -> np.ndarray:
        embedding = getattr(face.payload, "embedding", None)
        if embedding is None:
            raise RuntimeError("InsightFace detection did not include an embedding.")
        return np.asarray(embedding, dtype=np.float32).reshape(-1)
