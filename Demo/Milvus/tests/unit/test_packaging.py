from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_project_entrypoints_and_runtime_dependencies_are_declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"] == {
        "knowledge-api": "knowledge_service.api.app:main",
        "knowledge-admin": "knowledge_service.cli:main",
    }
    runtime = (ROOT / "requirements" / "runtime.txt").read_text(encoding="utf-8")
    assert "sentence-transformers==5.1.2" in runtime
    assert "pymilvus==2.6.17" in runtime


def test_compose_enforces_gpu_and_persistence_boundaries():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    knowledge = compose["services"]["knowledge"]
    assert compose["services"]["milvus"]["image"] == "milvusdb/milvus:v2.6.22"
    assert compose["services"]["milvus"]["pull_policy"] == "never"
    assert knowledge["pull_policy"] == "never"

    assert "./imports/imports:/imports:ro" in knowledge["volumes"]
    assert "./imports/unsupported:/unsupported" in knowledge["volumes"]
    assert "./imports/failed:/failed" in knowledge["volumes"]
    assert "./imports:/imports:ro" not in knowledge["volumes"]
    assert "./runtime/knowledge:/data" in knowledge["volumes"]
    assert "milvus-data:/var/lib/milvus" in compose["services"]["milvus"]["volumes"]
    reservation = knowledge["deploy"]["resources"]["reservations"]["devices"][0]
    assert reservation["driver"] == "nvidia"
    assert reservation["count"] == 1
    assert reservation["capabilities"] == ["gpu"]
    assert knowledge["environment"]["KNOWLEDGE_EMBEDDING_DEVICE"] == "cuda:0"
    assert compose["volumes"]["milvus-data"] == {
        "name": "milvus-data-recovered-20260808"
    }


def test_compose_keeps_stock_milvus_config_and_enables_embedded_etcd():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    milvus = compose["services"]["milvus"]

    assert milvus["environment"] == {
        "DEPLOY_MODE": "STANDALONE",
        "ETCD_USE_EMBED": "true",
        "ETCD_DATA_DIR": "/var/lib/milvus/etcd",
        "ETCD_CONFIG_PATH": "/milvus/configs/embedEtcd.yaml",
        "COMMON_STORAGETYPE": "local",
    }
    assert "./config/milvus.yaml:/milvus/configs/milvus.yaml:ro" not in milvus["volumes"]
    assert "./config/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml:ro" in milvus["volumes"]
    assert "milvus-data:/var/lib/milvus" in milvus["volumes"]


def test_image_pins_cuda_torch_and_model_revision_for_offline_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "torch==2.12.0" in dockerfile
    assert "https://download.pytorch.org/whl/cu130" in dockerfile
    assert "5617a9f61b028005a4858fdac845db406aefb181" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "COPY --from=model-fetch" in dockerfile


def test_runtime_and_import_payloads_are_git_ignored():
    ignores = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "imports/*" in ignores
    assert "runtime/*" in ignores
    assert "!imports/.gitkeep" not in ignores
    assert "!imports/imports/.gitkeep" in ignores
    assert "!imports/unsupported/.gitkeep" in ignores
    assert "!imports/failed/.gitkeep" in ignores
    assert "!runtime/.gitkeep" in ignores
    assert not (ROOT / "imports" / ".gitkeep").exists()
