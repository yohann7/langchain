import json
from pathlib import Path

from knowledge_service.api.app import create_api


def test_openapi_matches_versioned_v1_contract() -> None:
    expected = json.loads(Path("contracts/knowledge-api-v1.json").read_text(encoding="utf-8"))
    assert create_api().openapi() == expected

