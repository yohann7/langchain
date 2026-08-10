"""GPU-only BGE-M3 embedding provider."""

from __future__ import annotations

from threading import Lock
from typing import Any, Callable


class CudaUnavailableError(RuntimeError):
    """CUDA is mandatory but unavailable or misconfigured."""


class CudaBgeM3Embedder:
    def __init__(
        self,
        *,
        model_id: str,
        revision: str,
        dimension: int,
        batch_size: int,
        device: str,
        required_gpu_name: str | None = None,
        cache_folder: str | None = None,
        torch_module: Any | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self.revision = revision
        self.dimension = int(dimension)
        self.batch_size = int(batch_size)
        self.device = device
        self.required_gpu_name = required_gpu_name.strip() if required_gpu_name else None
        self.cache_folder = cache_folder
        self._torch = torch_module
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = Lock()
        self._encode_lock = Lock()
        self._verified = False

    def _torch_module(self):
        if self._torch is None:
            import torch

            self._torch = torch
        return self._torch

    def _default_model_factory(self, **values: Any):
        from sentence_transformers import SentenceTransformer

        model_id = values.pop("model_id")
        return SentenceTransformer(model_id, **values)

    def initialize(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            torch_module = self._torch_module()
            if not self.device.startswith("cuda") or not torch_module.cuda.is_available():
                raise CudaUnavailableError("CUDA is required for BGE-M3 embeddings")
            device_index = int(self.device.split(":", 1)[1]) if ":" in self.device else 0
            if self.required_gpu_name:
                actual_gpu_name = str(torch_module.cuda.get_device_name(device_index))
                if self.required_gpu_name.casefold() not in actual_gpu_name.casefold():
                    raise CudaUnavailableError(
                        f"GPU {self.required_gpu_name} is required; found {actual_gpu_name}"
                    )
            try:
                torch_module.empty(1, device=self.device)
            except Exception as exc:
                raise CudaUnavailableError(
                    f"Cannot allocate a CUDA tensor on {self.device}"
                ) from exc
            factory = self._model_factory or self._default_model_factory
            model = factory(
                model_id=self.model_id,
                revision=self.revision,
                device=self.device,
                cache_folder=self.cache_folder,
                local_files_only=True,
                trust_remote_code=False,
            )
            expected_index = device_index
            found_parameter = False
            for parameter in model.parameters():
                found_parameter = True
                parameter_device = getattr(parameter, "device", None)
                if (
                    parameter_device is None
                    or getattr(parameter_device, "type", None) != "cuda"
                    or getattr(parameter_device, "index", expected_index) != expected_index
                ):
                    raise CudaUnavailableError(
                        f"BGE-M3 model parameters are not entirely on {self.device}"
                    )
            if not found_parameter:
                raise CudaUnavailableError("BGE-M3 model exposes no device parameters")
            self._model = model
            return model

    def verify(self) -> None:
        if self._verified:
            return
        vector = self.embed_query("GPU readiness probe")
        if len(vector) != self.dimension:
            raise CudaUnavailableError("BGE-M3 readiness vector has an invalid dimension")
        self._verified = True

    def is_ready(self) -> bool:
        if self._verified:
            return True
        try:
            self.verify()
            return True
        except Exception:
            return False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.initialize()
        with self._encode_lock:
            batch_size = self.batch_size
            while True:
                try:
                    raw = model.encode(
                        texts,
                        batch_size=batch_size,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                    )
                    break
                except self._torch_module().cuda.OutOfMemoryError:
                    if batch_size <= 1:
                        raise
                    batch_size = max(1, batch_size // 2)
                    self._torch_module().cuda.empty_cache()
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        result = [[float(value) for value in vector] for vector in raw]
        if len(result) != len(texts):
            raise ValueError("BGE-M3 output count does not match the input count")
        if any(len(vector) != self.dimension for vector in result):
            raise ValueError("BGE-M3 output dimension does not match configuration")
        if any(abs(sum(value * value for value in vector) - 1.0) > 1e-3 for vector in result):
            raise ValueError("BGE-M3 output vectors are not normalized")
        return result

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("embedding query must not be blank")
        return self.embed_documents([text])[0]
