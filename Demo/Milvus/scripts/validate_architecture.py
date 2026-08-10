"""Fast static checks for the target's storage, GPU, and secret boundaries."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def main() -> int:
    errors: list[str] = []
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    knowledge = compose["services"]["knowledge"]
    milvus = compose["services"]["milvus"]
    if "./imports/imports:/imports:ro" not in knowledge["volumes"]:
        errors.append("imports/imports must be mounted read-only")
    if "./imports/unsupported:/unsupported" not in knowledge["volumes"]:
        errors.append("imports/unsupported must be mounted writable")
    if "./imports/failed:/failed" not in knowledge["volumes"]:
        errors.append("imports/failed must be mounted writable")
    if "./imports:/imports:ro" in knowledge["volumes"]:
        errors.append("legacy imports root mount must not be used")
    if "./runtime/knowledge:/data" not in knowledge["volumes"]:
        errors.append("runtime/knowledge must be mounted at /data")
    if "milvus-data:/var/lib/milvus" not in milvus["volumes"]:
        errors.append("Milvus must use the named volume")
    devices = knowledge["deploy"]["resources"]["reservations"]["devices"]
    if not devices or "gpu" not in devices[0].get("capabilities", []):
        errors.append("knowledge must reserve an NVIDIA GPU")
    if knowledge["environment"].get("KNOWLEDGE_EMBEDDING_DEVICE") != "cuda:0":
        errors.append("embedding device must be cuda:0")
    configuration = yaml.safe_load(
        (ROOT / "config" / "knowledge.yaml").read_text(encoding="utf-8")
    )
    if configuration["embedding"].get("required_gpu_name") != "NVIDIA GeForce RTX 4070 Laptop GPU":
        errors.append("embedding must require the RTX 4070 Laptop GPU")
    if REVISION not in (ROOT / "Dockerfile").read_text(encoding="utf-8"):
        errors.append("Docker image does not pin the approved BGE-M3 revision")
    if (ROOT / "migrations").exists():
        errors.append("empty or legacy migrations directory must not exist")
    for directory in (ROOT / "imports", ROOT / "runtime"):
        for item in directory.rglob("*"):
            if item.is_file() and item.name not in {".gitkeep", "README.md"}:
                errors.append(f"runtime payload must not be checked in: {item.relative_to(ROOT)}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("ARCHITECTURE_VALIDATION=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
