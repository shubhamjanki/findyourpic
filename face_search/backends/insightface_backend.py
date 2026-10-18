"""InsightFace ArcFace backend, preferring GPU inference when available."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from face_search.backends.base import BoundingBox, FaceBackend, FaceDetection


class InsightFaceBackend(FaceBackend):
    def __init__(self, model_name: str | None = None) -> None:
        import os
        if model_name is None:
            model_name = os.environ.get("INSIGHTFACE_MODEL", "buffalo_s")
        candidate_roots = [
            Path.home() / ".insightface",
            Path("/root/.insightface"),
            Path("/data/.insightface"),
            Path("/app/.insightface"),
        ]
        model_dir = None
        for root in candidate_roots:
            candidate = root / "models" / model_name
            if candidate.is_dir() and any(candidate.glob("*.onnx")):
                model_dir = candidate
                break

        if model_dir is None:
            target_root = Path.home() / ".insightface"
            model_dir = target_root / "models" / model_name
            model_dir.mkdir(parents=True, exist_ok=True)
            import urllib.request
            import zipfile
            url = f"https://github.com/deepinsight/insightface/releases/download/v0.7/{model_name}.zip"
            zip_path = model_dir.parent / f"{model_name}.zip"
            try:
                urllib.request.urlretrieve(url, zip_path)
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(model_dir)
                zip_path.unlink(missing_ok=True)
            except Exception as exc:
                raise RuntimeError(
                    f"InsightFace model '{model_name}' could not be downloaded to {model_dir}: {exc}"
                ) from exc

        root_dir = model_dir.parent.parent

        import os
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
        os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

        try:
            from insightface.app import FaceAnalysis
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "InsightFace is unavailable. Install the project dependencies first."
            ) from exc

        # On Windows, register pip-installed nvidia CUDA and cuDNN DLL directories
        try:
            import site
            candidates = []
            for sp in site.getsitepackages():
                candidates.extend([
                    os.path.join(sp, "nvidia", "cu13", "bin", "x86_64"),
                    os.path.join(sp, "nvidia", "cu13", "bin"),
                    os.path.join(sp, "nvidia", "cudnn", "bin"),
                ])
            for p in candidates:
                if os.path.isdir(p):
                    if hasattr(os, "add_dll_directory"):
                        try:
                            os.add_dll_directory(p)
                        except OSError:
                            pass
                    os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass

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
            root=str(root_dir),
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        # Use 320x320 detection to reduce memory on constrained environments (Render 512MB)
        det = int(os.environ.get("INSIGHTFACE_DET_SIZE", "320"))
        self._app.prepare(ctx_id=ctx_id, det_size=(det, det))

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
