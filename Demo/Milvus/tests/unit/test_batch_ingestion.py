from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_service.batch_ingestion import (
    BatchIngestionError,
    BatchIngestionService,
)
from knowledge_service.models import IngestResult


class RecordingIngestion:
    def __init__(
        self,
        *,
        statuses: dict[str, str] | None = None,
        fail_names: set[str] | None = None,
    ) -> None:
        self.statuses = statuses or {}
        self.fail_names = fail_names or set()
        self.calls: list[dict[str, object]] = []

    def ingest(self, **values: object) -> IngestResult:
        self.calls.append(values)
        name = Path(str(values["path"])).name
        if name in self.fail_names:
            raise ValueError("cannot parse document\ninternal detail")
        return IngestResult(
            status=self.statuses.get(name, "active"),
            chunks=1,
        )


def _settings(imports_root: Path):
    return SimpleNamespace(allowed_roots=[imports_root])


def test_ingest_folder_recurses_classifies_and_continues(tmp_path: Path) -> None:
    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    (source / "nested").mkdir(parents=True)
    (source / "good.txt").write_text("good", encoding="utf-8")
    (source / "duplicate.csv").write_text("duplicate", encoding="utf-8")
    (source / "nested" / "same.md").write_text("same", encoding="utf-8")
    (source / "nested" / "bad.pdf").write_bytes(b"broken")
    (source / "tool.exe").write_bytes(b"binary")

    ingestion = RecordingIngestion(
        statuses={"duplicate.csv": "duplicate", "same.md": "unchanged"},
        fail_names={"bad.pdf"},
    )
    service = BatchIngestionService(_settings(imports_root), ingestion)

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=tmp_path / "unsupported",
        failed_dir=tmp_path / "failed",
    )

    assert [Path(str(call["path"])).relative_to(source).as_posix() for call in ingestion.calls] == [
        "duplicate.csv",
        "good.txt",
        "nested/bad.pdf",
        "nested/same.md",
    ]
    assert (tmp_path / "unsupported" / "project-a" / "tool.exe").read_bytes() == b"binary"
    assert (tmp_path / "failed" / "project-a" / "nested" / "bad.pdf").read_bytes() == b"broken"
    assert (source / "tool.exe").exists()
    assert (source / "nested" / "bad.pdf").exists()
    assert result.counts == {
        "scanned": 5,
        "imported": 1,
        "unchanged": 1,
        "duplicate": 1,
        "unsupported": 1,
        "failed": 1,
    }
    assert result.status == "completed_with_failures"
    assert result.has_failures is True
    assert result.unsupported_files[0].source.endswith("tool.exe")
    assert result.failed_files[0].error == "INVALID_DOCUMENT"


def test_ingest_folder_overwrites_an_existing_quarantine_copy(tmp_path: Path) -> None:
    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    unsupported = source / "tool.exe"
    unsupported.write_bytes(b"first")
    quarantine = tmp_path / "unsupported"
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )
    unsupported.write_bytes(b"second")
    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )

    assert (quarantine / "project-a" / "tool.exe").read_bytes() == b"second"
    assert result.counts["unsupported"] == 1
    assert result.has_failures is False


def test_ingest_folder_rejects_a_source_outside_allowed_roots(tmp_path: Path) -> None:
    imports_root = tmp_path / "imports"
    imports_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    with pytest.raises(BatchIngestionError, match="outside configured imports roots"):
        service.ingest_folder(
            owner_id="local-user",
            knowledge_base="personal",
            source_dir=outside,
            unsupported_dir=tmp_path / "unsupported",
            failed_dir=tmp_path / "failed",
        )


def test_ingest_folder_records_quarantine_copy_failure_and_keeps_importing(
    tmp_path: Path,
) -> None:
    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    (source / "good.txt").write_text("good", encoding="utf-8")
    (source / "tool.exe").write_bytes(b"binary")
    blocked_root = tmp_path / "blocked"
    blocked_root.write_text("not a directory", encoding="utf-8")
    ingestion = RecordingIngestion()
    service = BatchIngestionService(_settings(imports_root), ingestion)

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=blocked_root,
        failed_dir=tmp_path / "failed",
    )

    assert [Path(str(call["path"])).name for call in ingestion.calls] == ["good.txt"]
    assert result.counts["imported"] == 1
    assert result.counts["unsupported"] == 1
    assert result.counts["failed"] == 1
    assert result.has_failures is True
    assert result.failed_files[0].source.endswith("tool.exe")


def test_ingest_folder_does_not_follow_a_quarantine_batch_symlink(
    tmp_path: Path,
) -> None:
    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    (source / "tool.exe").write_bytes(b"binary")
    quarantine = tmp_path / "unsupported"
    quarantine.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    try:
        (quarantine / "project-a").symlink_to(redirected, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )

    assert not (redirected / "tool.exe").exists()
    assert result.counts["unsupported"] == 1
    assert result.counts["failed"] == 1
    assert result.failed_files[0].error == "QUARANTINE_COPY_FAILED"


@pytest.mark.skipif(__import__("os").name == "posix", reason="path-check fallback test")
def test_ingest_folder_rejects_a_quarantine_component_reported_as_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import knowledge_service.batch_ingestion as batch_module

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    (source / "tool.exe").write_bytes(b"binary")
    quarantine = tmp_path / "unsupported"
    batch_root = quarantine / "project-a"
    original_is_link = batch_module._is_link
    monkeypatch.setattr(
        batch_module,
        "_is_link",
        lambda path: path == batch_root or original_is_link(path),
    )
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )

    assert not (batch_root / "tool.exe").exists()
    assert result.counts["failed"] == 1
    assert result.failed_files[0].error == "QUARANTINE_COPY_FAILED"


@pytest.mark.parametrize("source_kind", ["missing", "file"])
def test_ingest_folder_reports_invalid_source_kinds(
    tmp_path: Path,
    source_kind: str,
) -> None:
    imports_root = tmp_path / "imports"
    imports_root.mkdir()
    source = imports_root / source_kind
    if source_kind == "file":
        source.write_text("not a directory", encoding="utf-8")
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    with pytest.raises(BatchIngestionError):
        service.ingest_folder(
            owner_id="local-user",
            knowledge_base="personal",
            source_dir=source,
            unsupported_dir=tmp_path / "unsupported",
            failed_dir=tmp_path / "failed",
        )


def test_ingest_folder_converts_directory_scan_errors_to_batch_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import knowledge_service.batch_ingestion as batch_module

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    def denied_walk(_source, *, followlinks, onerror=None):
        del followlinks, onerror
        raise PermissionError("secret filesystem detail")

    monkeypatch.setattr(batch_module.os, "walk", denied_walk)

    with pytest.raises(BatchIngestionError, match="cannot be scanned"):
        service.ingest_folder(
            owner_id="local-user",
            knowledge_base="personal",
            source_dir=source,
            unsupported_dir=tmp_path / "unsupported",
            failed_dir=tmp_path / "failed",
        )


def test_ingest_folder_skips_non_regular_directory_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import knowledge_service.batch_ingestion as batch_module

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    (source / "good.txt").write_text("good", encoding="utf-8")
    (source / "named-pipe").write_text("placeholder", encoding="utf-8")
    original_is_regular = batch_module._is_regular_file
    monkeypatch.setattr(
        batch_module,
        "_is_regular_file",
        lambda path: path.name != "named-pipe" and original_is_regular(path),
    )
    ingestion = RecordingIngestion()
    service = BatchIngestionService(_settings(imports_root), ingestion)

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=tmp_path / "unsupported",
        failed_dir=tmp_path / "failed",
    )

    assert [Path(str(call["path"])).name for call in ingestion.calls] == ["good.txt"]
    assert result.counts["scanned"] == 1


def test_ingest_folder_converts_invalid_allowed_root_to_batch_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "imports" / "project-a"
    source.mkdir(parents=True)
    missing_allowed_root = tmp_path / "missing-imports"
    service = BatchIngestionService(
        _settings(missing_allowed_root),
        RecordingIngestion(),
    )

    with pytest.raises(BatchIngestionError, match="Configured imports root is unavailable"):
        service.ingest_folder(
            owner_id="local-user",
            knowledge_base="personal",
            source_dir=source,
            unsupported_dir=tmp_path / "unsupported",
            failed_dir=tmp_path / "failed",
        )


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX no-follow test")
def test_quarantine_copy_resists_batch_directory_swap_before_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import knowledge_service.batch_ingestion as batch_module

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    (source / "tool.exe").write_bytes(b"binary")
    quarantine = tmp_path / "unsupported"
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    original_copy = batch_module._copy_regular_no_follow

    def swap_then_copy(source_path, destination, quarantine_root):
        batch_root = quarantine_root / "project-a"
        if batch_root.exists():
            batch_root.rmdir()
        batch_root.symlink_to(redirected, target_is_directory=True)
        return original_copy(source_path, destination, quarantine_root)

    monkeypatch.setattr(batch_module, "_copy_regular_no_follow", swap_then_copy)
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )

    assert not (redirected / "tool.exe").exists()
    assert result.counts["failed"] == 1
    assert result.failed_files[0].error == "QUARANTINE_COPY_FAILED"


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX FIFO test")
def test_quarantine_copy_rejects_fifo_swap_without_blocking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import os
    import knowledge_service.batch_ingestion as batch_module

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    unsupported = source / "tool.exe"
    unsupported.write_bytes(b"binary")
    original_copy = batch_module._copy_regular_no_follow

    def fifo_then_copy(source_path, destination, quarantine_root):
        source_path.unlink()
        os.mkfifo(source_path)
        return original_copy(source_path, destination, quarantine_root)

    monkeypatch.setattr(batch_module, "_copy_regular_no_follow", fifo_then_copy)
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=tmp_path / "unsupported",
        failed_dir=tmp_path / "failed",
    )

    assert result.counts["failed"] == 1
    assert result.failed_files[0].error == "QUARANTINE_COPY_FAILED"


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX mode test")
def test_quarantine_copy_clears_special_permission_bits(tmp_path: Path) -> None:
    import stat

    imports_root = tmp_path / "imports"
    source = imports_root / "project-a"
    source.mkdir(parents=True)
    unsupported = source / "tool.exe"
    unsupported.write_bytes(b"binary")
    unsupported.chmod(0o7755)
    quarantine = tmp_path / "unsupported"
    service = BatchIngestionService(_settings(imports_root), RecordingIngestion())

    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=quarantine,
        failed_dir=tmp_path / "failed",
    )

    copied_mode = stat.S_IMODE(
        (quarantine / "project-a" / "tool.exe").stat().st_mode
    )
    assert result.counts["failed"] == 0
    assert copied_mode == 0o755
