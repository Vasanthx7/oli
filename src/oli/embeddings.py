"""Local text embeddings via fastembed (ONNX, CPU, no API key, no PyTorch).

Uses BAAI/bge-small-en-v1.5 (384-dim). The model (~90MB) downloads once on first
use into data/models/ so it persists across runs and stays out of the system temp
dir. bge is an asymmetric retrieval model: documents are embedded plainly, queries
via query_embed (which adds the recommended retrieval prefix) for a wider
relevant-vs-irrelevant similarity gap.
"""

import os
import threading

import numpy as np

from .config import DATA_DIR

# Quiet the Windows symlink caching warning from huggingface_hub.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
_CACHE_DIR = DATA_DIR / "models"


class Embedder:
    """Lazy singleton wrapper around a fastembed TextEmbedding model."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "Embedder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    self._model = TextEmbedding(
                        model_name=MODEL_NAME, cache_dir=str(_CACHE_DIR)
                    )
        return self._model

    def embed_document(self, text: str) -> np.ndarray:
        model = self._ensure_model()
        vec = next(iter(model.embed([text])))
        return np.asarray(vec, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        model = self._ensure_model()
        vec = next(iter(model.query_embed([text])))
        return np.asarray(vec, dtype=np.float32)


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)
