"""Regenerate the checked-in OpenAPI v1 contract without loading CUDA or Milvus."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_service.api.app import create_api  # noqa: E402


def main() -> None:
    destination = ROOT / "contracts" / "knowledge-api-v1.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(create_api().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()

