from contextlib import nullcontext
from dataclasses import dataclass
import json
from types import SimpleNamespace

from knowledge_service.batch_ingestion import (
    BatchIngestionError,
    BatchIngestionService,
    BatchIngestResult,
)


def test_status_command_emits_machine_readable_json(monkeypatch, capsys):
    from knowledge_service import cli

    @dataclass
    class Coordinator:
        def read(self):
            return nullcontext()

    class Management:
        def status(self, *, owner_id):
            assert owner_id == "alice"
            return {"enabled": True, "owner": owner_id}

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "management": Management()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["--config", "custom.yaml", "status", "--user-id", "alice"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {"enabled": True, "owner": "alice"}


def test_restore_requires_successful_rebuild_before_reporting_success(monkeypatch, capsys):
    from knowledge_service import cli

    events = []

    class Coordinator:
        def maintenance(self):
            return nullcontext()

    class Archive:
        def restore_from(self, path):
            events.append(("restore", path))
            return {"status": "restored"}

    class Management:
        def rebuild_index(self):
            events.append(("rebuild", None))
            return {"status": "rebuilt", "documents": 2, "chunks": 8}

    services = type(
        "Services",
        (),
        {
            "coordinator": Coordinator(),
            "archive": Archive(),
            "management": Management(),
        },
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["restore", "backup.zip"])

    assert result == 0
    assert events == [("restore", "backup.zip"), ("rebuild", None)]
    assert json.loads(capsys.readouterr().out)["rebuild"]["status"] == "rebuilt"


def test_ingest_defaults_to_local_user_and_personal(monkeypatch, capsys) -> None:
    from knowledge_service import cli

    recorded: dict[str, object] = {}

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class Ingestion:
        def ingest(self, **values):
            recorded.update(values)
            return {"status": "active", "chunks": 2}

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "ingestion": Ingestion()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["ingest", "/imports/manual.pdf"])

    assert result == 0
    assert recorded == {
        "owner_id": "local-user",
        "knowledge_base": "personal",
        "path": "/imports/manual.pdf",
        "request_id": None,
    }
    assert json.loads(capsys.readouterr().out) == {"chunks": 2, "status": "active"}


def test_ingest_folder_uses_defaults_and_returns_one_for_failed_batch(
    monkeypatch,
    capsys,
) -> None:
    from knowledge_service import cli

    recorded: dict[str, object] = {}

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class BatchIngestion:
        def ingest_folder(self, **values):
            recorded.update(values)
            return BatchIngestResult(
                status="completed_with_failures",
                source_directory="/imports/project-a",
                unsupported_directory="/unsupported/project-a",
                failed_directory="/failed/project-a",
                counts={
                    "scanned": 1,
                    "imported": 0,
                    "unchanged": 0,
                    "duplicate": 0,
                    "unsupported": 0,
                    "failed": 1,
                },
                unsupported_files=[],
                failed_files=[],
            )

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "batch_ingestion": BatchIngestion()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["ingest-folder", "/imports/project-a"])

    assert result == 1
    assert recorded == {
        "owner_id": "local-user",
        "knowledge_base": "personal",
        "source_dir": "/imports/project-a",
        "unsupported_dir": "/unsupported",
        "failed_dir": "/failed",
    }
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed_with_failures"
    assert output["counts"]["failed"] == 1


def test_ingest_folder_allows_overriding_every_default(monkeypatch) -> None:
    from knowledge_service import cli

    recorded: dict[str, object] = {}

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class BatchIngestion:
        def ingest_folder(self, **values):
            recorded.update(values)
            return BatchIngestResult(
                status="completed",
                source_directory=str(values["source_dir"]),
                unsupported_directory=str(values["unsupported_dir"]),
                failed_directory=str(values["failed_dir"]),
                counts={
                    "scanned": 0,
                    "imported": 0,
                    "unchanged": 0,
                    "duplicate": 0,
                    "unsupported": 0,
                    "failed": 0,
                },
                unsupported_files=[],
                failed_files=[],
            )

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "batch_ingestion": BatchIngestion()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(
        [
            "ingest-folder",
            "/imports/project-b",
            "--user-id",
            "alice",
            "--knowledge-base",
            "work",
            "--unsupported-dir",
            "/quarantine/unsupported",
            "--failed-dir",
            "/quarantine/failed",
        ]
    )

    assert result == 0
    assert recorded == {
        "owner_id": "alice",
        "knowledge_base": "work",
        "source_dir": "/imports/project-b",
        "unsupported_dir": "/quarantine/unsupported",
        "failed_dir": "/quarantine/failed",
    }


def test_ingest_folder_reports_invalid_source_as_json(monkeypatch, capsys) -> None:
    from knowledge_service import cli

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class BatchIngestion:
        def ingest_folder(self, **_values):
            raise BatchIngestionError("Batch source directory does not exist")

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "batch_ingestion": BatchIngestion()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["ingest-folder", "/imports/missing"])

    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error": "Batch source directory does not exist",
    }


def test_ingest_folder_returns_zero_when_only_unsupported_files_exist(
    monkeypatch,
) -> None:
    from knowledge_service import cli

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class BatchIngestion:
        def ingest_folder(self, **_values):
            return BatchIngestResult(
                status="completed",
                source_directory="/imports/project-a",
                unsupported_directory="/unsupported/project-a",
                failed_directory="/failed/project-a",
                counts={
                    "scanned": 1,
                    "imported": 0,
                    "unchanged": 0,
                    "duplicate": 0,
                    "unsupported": 1,
                    "failed": 0,
                },
                unsupported_files=[],
                failed_files=[],
            )

    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "batch_ingestion": BatchIngestion()},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    assert cli.main(["ingest-folder", "/imports/project-a"]) == 0


def test_ingest_folder_reports_unavailable_configured_root_as_json(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from knowledge_service import cli

    source = tmp_path / "imports" / "project-a"
    source.mkdir(parents=True)

    class Coordinator:
        def mutation(self):
            return nullcontext()

    class UnusedIngestion:
        def ingest(self, **_values):  # pragma: no cover - empty source
            raise AssertionError("ingestion should not run")

    batch = BatchIngestionService(
        SimpleNamespace(allowed_roots=[tmp_path / "missing-imports"]),
        UnusedIngestion(),
    )
    services = type(
        "Services",
        (),
        {"coordinator": Coordinator(), "batch_ingestion": batch},
    )()
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "create_services", lambda settings: services)

    result = cli.main(["ingest-folder", str(source)])

    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "error": "Configured imports root is unavailable",
    }
