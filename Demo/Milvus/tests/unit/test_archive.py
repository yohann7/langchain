from pathlib import Path
from hashlib import sha256
from contextlib import closing
import json
import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from knowledge_service.archive import ArchiveManager
from knowledge_service.config import KnowledgeSettings
from knowledge_service.storage.sqlite import SqliteDatabase
import knowledge_service.archive as archive_module


def _settings(tmp_path: Path) -> KnowledgeSettings:
    return KnowledgeSettings(run_dir=tmp_path / "runtime" / "knowledge")


def test_new_archive_round_trip_creates_reindex_marker(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    SqliteDatabase(settings.database_path)
    document = settings.documents_path / "doc-1" / "manual.txt"
    document.parent.mkdir(parents=True)
    document.write_text("managed content", encoding="utf-8")
    manager = ArchiveManager(settings)

    exported = manager.export_to("backup.zip")
    archive_path = Path(exported["path"])
    with ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == "xiaoxu-knowledge"
    assert manifest["schema_version"] == 3
    assert manifest["embedding"]["revision"] == settings.embedding_revision

    document.write_text("changed", encoding="utf-8")
    restored = manager.restore_from("backup.zip")

    assert restored["reindex_required"] is True
    assert settings.reindex_marker.is_file()
    assert document.read_text(encoding="utf-8") == "managed content"


def test_archive_rejects_legacy_and_tampered_payloads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.transfers_path.mkdir(parents=True)
    SqliteDatabase(settings.database_path)
    manager = ArchiveManager(settings)

    legacy = settings.transfers_path / "legacy.zip"
    with ZipFile(legacy, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("knowledge.db", b"legacy")
    with pytest.raises(ValueError, match="manifest"):
        manager.restore_from("legacy.zip")

    exported = Path(manager.export_to("valid.zip")["path"])
    with ZipFile(exported, "a", compression=ZIP_DEFLATED) as archive:
        archive.writestr("documents/tampered.txt", "not in manifest")
    with pytest.raises(ValueError, match="inventory"):
        manager.restore_from("valid.zip")


def test_import_rejects_database_with_wrong_schema(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.transfers_path.mkdir(parents=True)
    SqliteDatabase(settings.database_path)
    manager = ArchiveManager(settings)
    archive_path = Path(manager.export_to("wrong-schema.zip")["path"])

    staging = tmp_path / "staging"
    staging.mkdir()
    with ZipFile(archive_path) as archive:
        archive.extractall(staging)
    with closing(sqlite3.connect(staging / "knowledge.db")) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "UPDATE schema_metadata SET value='2' WHERE key='schema_version'"
        )
        connection.commit()
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["knowledge.db"] = sha256(
        (staging / "knowledge.db").read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    archive_path.unlink()
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())

    with pytest.raises(ValueError, match="schema version"):
        manager.restore_from("wrong-schema.zip")


def test_restore_rejects_different_embedding_or_chunking_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    SqliteDatabase(settings.database_path)
    manager = ArchiveManager(settings)
    archive_path = Path(manager.export_to("wrong-contract.zip")["path"])

    staging = tmp_path / "contract-staging"
    staging.mkdir()
    with ZipFile(archive_path) as archive:
        archive.extractall(staging)
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["embedding"]["revision"] = "unapproved"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive_path.unlink()
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())

    with pytest.raises(ValueError, match="contract"):
        manager.restore_from("wrong-contract.zip")


def test_failed_first_restore_removes_partially_replaced_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    donor = _settings(tmp_path / "donor")
    SqliteDatabase(donor.database_path)
    donor_document = donor.documents_path / "doc-1" / "ver-1" / "manual.txt"
    donor_document.parent.mkdir(parents=True)
    donor_document.write_text("managed", encoding="utf-8")
    donor_archive = Path(ArchiveManager(donor).export_to("backup.zip")["path"])

    target = _settings(tmp_path / "target")
    target.transfers_path.mkdir(parents=True)
    target_archive = target.transfers_path / "backup.zip"
    target_archive.write_bytes(donor_archive.read_bytes())
    original_copytree = archive_module.shutil.copytree

    def fail_imported_documents(source, destination, *args, **kwargs):
        if Path(source).name == "documents" and Path(destination) == target.documents_path:
            raise OSError("simulated copy failure")
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(archive_module.shutil, "copytree", fail_imported_documents)

    with pytest.raises(OSError, match="simulated"):
        ArchiveManager(target).restore_from("backup.zip")

    assert not target.database_path.exists()
    assert not target.reindex_marker.exists()
