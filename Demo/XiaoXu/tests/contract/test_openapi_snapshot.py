import json
from pathlib import Path

from private_agent.core.settings import AppSettings
from private_agent.interfaces.api.app import create_app


def test_openapi_snapshot_matches_xiaoxu_producer(tmp_path):
    expected = json.loads(
        Path("contracts/openapi-v1.json").read_text(encoding="utf-8")
    )
    actual = create_app(
        AppSettings(
            run_dir=tmp_path,
            api_token="contract-test-token",
        ),
        agent=object(),
    ).openapi()

    assert actual == expected
