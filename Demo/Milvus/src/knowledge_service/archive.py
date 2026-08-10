"""Versioned, hash-inventoried whole-knowledge archive operations."""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from knowledge_service.config import KnowledgeSettings
from knowledge_service.ingestion import IngestionService
from knowledge_service.storage.sqlite import SqliteDatabase


class ArchiveManager:
    FORMAT = "xiaoxu-knowledge"
    FORMAT_VERSION = 1

    def __init__(self, settings: KnowledgeSettings) -> None:
        self.settings = settings

    def export_to(self, destination: str | Path) -> dict[str, object]:
        target = self._transfer_path(destination, require_exists=False)
        if target.suffix.lower() != ".zip":
            raise ValueError("transfer archive must use a .zip suffix")
        target.parent.mkdir(parents=True, exist_ok=True)
        export_id = uuid4().hex
        with TemporaryDirectory(dir=self.settings.run_dir) as temporary:
            stage = Path(temporary)
            database = stage / "knowledge.db"
            _sqlite_backup(self.settings.database_path, database)
            if self.settings.documents_path.exists():
                shutil.copytree(self.settings.documents_path, stage / "documents")
            files = {
                path.relative_to(stage).as_posix(): sha256(path.read_bytes()).hexdigest()
                for path in sorted(stage.rglob("*"))
                if path.is_file()
            }
            manifest = {
                "format": self.FORMAT,
                "format_version": self.FORMAT_VERSION,
                "schema_version": SqliteDatabase.SCHEMA_VERSION,
                "parser": {"version": IngestionService.PARSER_VERSION},
                "embedding": {
                    "model": self.settings.embedding_model,
                    "revision": self.settings.embedding_revision,
                    "dimension": self.settings.embedding_dimension,
                },
                "chunking": {
                    "version": IngestionService.CHUNKER_VERSION,
                    "size_chars": self.settings.rag_chunk_size_chars,
                    "overlap_chars": self.settings.rag_chunk_overlap_chars,
                },
                "files": files,
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            target.unlink(missing_ok=True)
            with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage).as_posix())
        return {"status": "exported", "path": str(target), "export_id": export_id}

    def restore_from(self, source: str | Path) -> dict[str, object]:
        archive_path = self._transfer_path(source, require_exists=True)
        import_id = uuid4().hex
        backup = self.settings.transfers_path / "backups" / import_id
        backup.mkdir(parents=True, exist_ok=False)
        had_database = self.settings.database_path.exists()
        had_documents = self.settings.documents_path.exists()
        with TemporaryDirectory(dir=self.settings.run_dir) as temporary:
            stage = Path(temporary)
            manifest = self._validate_and_extract(archive_path, stage)
            self._validate_contract(manifest)
            self._validate_database(stage / "knowledge.db", manifest)
            if had_database:
                _sqlite_backup(self.settings.database_path, backup / "knowledge.db")
            if had_documents:
                shutil.copytree(self.settings.documents_path, backup / "documents")
            self.settings.reindex_marker.write_text(import_id, encoding="utf-8")
            try:
                _sqlite_restore(stage / "knowledge.db", self.settings.database_path)
                shutil.rmtree(self.settings.documents_path, ignore_errors=True)
                imported_documents = stage / "documents"
                if imported_documents.exists():
                    shutil.copytree(imported_documents, self.settings.documents_path)
                else:
                    self.settings.documents_path.mkdir(parents=True, exist_ok=True)
            except Exception:
                if (backup / "knowledge.db").exists():
                    _sqlite_restore(backup / "knowledge.db", self.settings.database_path)
                else:
                    _remove_sqlite_files(self.settings.database_path)
                shutil.rmtree(self.settings.documents_path, ignore_errors=True)
                if (backup / "documents").exists():
                    shutil.copytree(backup / "documents", self.settings.documents_path)
                self.settings.reindex_marker.unlink(missing_ok=True)
                raise
        return {
            "status": "restored",
            "path": str(archive_path),
            "import_id": import_id,
            "backup_path": str(backup),
            "reindex_required": True,
        }

    def _validate_and_extract(self, source: Path, stage: Path) -> dict[str, object]:
        if source.stat().st_size > self.settings.maximum_archive_bytes:
            raise ValueError("archive exceeds the configured size limit")
        with ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > self.settings.maximum_archive_members:
                raise ValueError("archive contains too many members")
            expanded = sum(member.file_size for member in members)
            if expanded > self.settings.maximum_extracted_bytes:
                raise ValueError("archive expands beyond the configured size limit")
            names: set[str] = set()
            for member in members:
                path = Path(member.filename)
                mode = member.external_attr >> 16
                if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(mode):
                    raise ValueError("archive contains an unsafe path")
                if member.file_size and not member.compress_size:
                    raise ValueError("archive member has an invalid compression ratio")
                if (
                    member.compress_size
                    and member.file_size / member.compress_size
                    > self.settings.maximum_compression_ratio
                ):
                    raise ValueError("archive compression ratio exceeds the configured limit")
                normalized = path.as_posix()
                if normalized in names:
                    raise ValueError("archive contains duplicate members")
                names.add(normalized)
            if "manifest.json" not in names:
                raise ValueError("archive does not contain the required manifest")
            for member in members:
                if member.is_dir():
                    continue
                destination = stage / Path(member.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, destination.open("wb") as output:
                    shutil.copyfileobj(source_file, output)
        try:
            manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("archive manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise ValueError("archive manifest is invalid")
        if (
            manifest.get("format") != self.FORMAT
            or manifest.get("format_version") != self.FORMAT_VERSION
        ):
            raise ValueError("archive manifest format is unsupported")
        inventory = manifest.get("files")
        if not isinstance(inventory, dict):
            raise ValueError("archive manifest inventory is invalid")
        actual_names = {
            path.relative_to(stage).as_posix()
            for path in stage.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        if actual_names != set(inventory):
            raise ValueError("archive inventory does not match archive members")
        for name, expected_hash in inventory.items():
            actual_hash = sha256((stage / name).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("archive inventory hash validation failed")
        return manifest

    def _validate_contract(self, manifest: dict[str, object]) -> None:
        expected = {
            "parser": {"version": IngestionService.PARSER_VERSION},
            "embedding": {
                "model": self.settings.embedding_model,
                "revision": self.settings.embedding_revision,
                "dimension": self.settings.embedding_dimension,
            },
            "chunking": {
                "version": IngestionService.CHUNKER_VERSION,
                "size_chars": self.settings.rag_chunk_size_chars,
                "overlap_chars": self.settings.rag_chunk_overlap_chars,
            },
        }
        if any(manifest.get(name) != value for name, value in expected.items()):
            raise ValueError("archive processing contract is incompatible")

    def _validate_database(self, path: Path, manifest: dict[str, object]) -> None:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise ValueError("knowledge database failed SQLite integrity check")
        actual = int(row[0]) if row else -1
        expected = SqliteDatabase.SCHEMA_VERSION
        if actual != expected or manifest.get("schema_version") != expected:
            raise ValueError(
                f"archive schema version {actual} is unsupported; expected {expected}"
            )

    def _transfer_path(self, value: str | Path, *, require_exists: bool) -> Path:
        self.settings.transfers_path.mkdir(parents=True, exist_ok=True)
        supplied = Path(value).expanduser()
        candidate = (
            supplied.resolve(strict=False)
            if supplied.is_absolute()
            else (self.settings.transfers_path / supplied).resolve(strict=False)
        )
        try:
            candidate.relative_to(self.settings.transfers_path.resolve(strict=False))
        except ValueError as exc:
            raise ValueError("path must stay inside the managed transfer directory") from exc
        return candidate.resolve(strict=True) if require_exists else candidate


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(source_path)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination)


def _sqlite_restore(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path)) as source:
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination)


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)
