from dataclasses import dataclass, field

import pytest

from knowledge_service.embedding import CudaBgeM3Embedder, CudaUnavailableError


@dataclass
class _Device:
    type: str = "cuda"
    index: int = 0


@dataclass
class _Parameter:
    device: _Device = field(default_factory=_Device)


class _Cuda:
    class OutOfMemoryError(RuntimeError):
        pass

    def __init__(self, available: bool = True, device_name: str = "NVIDIA GeForce RTX 4070 Laptop GPU") -> None:
        self.available = available
        self.device_name = device_name
        self.empty_cache_calls = 0

    def is_available(self) -> bool:
        return self.available

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1

    def get_device_name(self, _index: int) -> str:
        return self.device_name


class _Torch:
    def __init__(self, available: bool = True, device_name: str = "NVIDIA GeForce RTX 4070 Laptop GPU") -> None:
        self.cuda = _Cuda(available, device_name)
        self.created_on: str | None = None

    def empty(self, _size: int, *, device: str):
        self.created_on = device
        return object()


class _Model:
    def __init__(self, torch_module: _Torch, maximum_batch: int = 2) -> None:
        self.torch = torch_module
        self.maximum_batch = maximum_batch
        self.batch_sizes: list[int] = []

    def parameters(self):
        yield _Parameter()

    def encode(self, texts, *, batch_size, normalize_embeddings, show_progress_bar):
        self.batch_sizes.append(batch_size)
        if batch_size > self.maximum_batch:
            raise self.torch.cuda.OutOfMemoryError("out of memory")
        assert normalize_embeddings is True
        assert show_progress_bar is False
        return [[1.0, 0.0, 0.0] for _ in texts]


class _MixedDeviceModel(_Model):
    def parameters(self):
        yield _Parameter(_Device("cuda", 0))
        yield _Parameter(_Device("cpu", 0))


class _UnnormalizedModel(_Model):
    def encode(self, texts, *, batch_size, normalize_embeddings, show_progress_bar):
        del batch_size, normalize_embeddings, show_progress_bar
        return [[2.0, 0.0, 0.0] for _ in texts]


def test_embedder_refuses_to_initialize_without_cuda() -> None:
    torch_module = _Torch(available=False)
    embedder = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3",
        revision="fixed",
        dimension=3,
        batch_size=4,
        device="cuda:0",
        torch_module=torch_module,
        model_factory=lambda **_kwargs: _Model(torch_module),
    )

    with pytest.raises(CudaUnavailableError):
        embedder.initialize()


def test_embedder_reduces_cuda_batch_without_cpu_fallback() -> None:
    torch_module = _Torch()
    model = _Model(torch_module, maximum_batch=2)
    embedder = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3",
        revision="fixed",
        dimension=3,
        batch_size=4,
        device="cuda:0",
        torch_module=torch_module,
        model_factory=lambda **_kwargs: model,
    )

    vectors = embedder.embed_documents(["one", "two"])

    assert vectors == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert model.batch_sizes == [4, 2]
    assert torch_module.cuda.empty_cache_calls == 1
    assert torch_module.created_on == "cuda:0"


def test_embedder_rejects_mixed_cpu_parameters_and_unnormalized_output() -> None:
    torch_module = _Torch()
    mixed = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3", revision="fixed", dimension=3,
        batch_size=4, device="cuda:0", torch_module=torch_module,
        model_factory=lambda **_kwargs: _MixedDeviceModel(torch_module),
    )
    with pytest.raises(CudaUnavailableError, match="parameters"):
        mixed.initialize()

    unnormalized = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3", revision="fixed", dimension=3,
        batch_size=1, device="cuda:0", torch_module=torch_module,
        model_factory=lambda **_kwargs: _UnnormalizedModel(torch_module),
    )
    with pytest.raises(ValueError, match="normalized"):
        unnormalized.embed_documents(["one"])


def test_oom_at_batch_one_is_propagated_without_cpu_fallback() -> None:
    torch_module = _Torch()
    model = _Model(torch_module, maximum_batch=0)
    embedder = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3", revision="fixed", dimension=3,
        batch_size=4, device="cuda:0", torch_module=torch_module,
        model_factory=lambda **_kwargs: model,
    )

    with pytest.raises(_Cuda.OutOfMemoryError):
        embedder.embed_documents(["one"])
    assert model.batch_sizes == [4, 2, 1]
    assert torch_module.created_on == "cuda:0"


def test_embedder_rejects_a_different_gpu_model() -> None:
    torch_module = _Torch(device_name="NVIDIA GeForce RTX 4060 Laptop GPU")
    embedder = CudaBgeM3Embedder(
        model_id="BAAI/bge-m3", revision="fixed", dimension=3,
        batch_size=4, device="cuda:0",
        required_gpu_name="NVIDIA GeForce RTX 4070 Laptop GPU",
        torch_module=torch_module,
        model_factory=lambda **_kwargs: _Model(torch_module),
    )

    with pytest.raises(CudaUnavailableError, match="RTX 4070"):
        embedder.initialize()
