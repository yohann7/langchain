"""Recursive folder ingestion orchestration for the administrative CLI."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import stat
from typing import Protocol

from knowledge_service.config import KnowledgeSettings
from knowledge_service.errors import (
    IngestionDeniedError,
    KnowledgeUnavailableError,
    UnsupportedDocumentError,
)
from knowledge_service.models import IngestResult
from knowledge_service.parsing.registry import SUPPORTED_SUFFIXES


class BatchIngestionError(ValueError):
    """A batch source or quarantine path violates the CLI contract."""


class SingleFileIngestion(Protocol):
    def ingest(self, **values: object) -> IngestResult: ...


@dataclass(frozen=True)
class BatchFileResult:
    source: str
    copied_to: str
    error: str | None = None


@dataclass(frozen=True)
class BatchIngestResult:
    status: str
    source_directory: str
    unsupported_directory: str
    failed_directory: str
    counts: dict[str, int]
    unsupported_files: list[BatchFileResult]
    failed_files: list[BatchFileResult]

    @property
    def has_failures(self) -> bool:
        return self.counts["failed"] > 0


class BatchIngestionService:
    def __init__(
        self,
        settings: KnowledgeSettings,
        ingestion: SingleFileIngestion,
    ) -> None:
        self.settings = settings
        self.ingestion = ingestion

    def ingest_folder(
        self,
        *,
        owner_id: str,
        knowledge_base: str,
        source_dir: str | Path,
        unsupported_dir: str | Path,
        failed_dir: str | Path,
    ) -> BatchIngestResult:
        source = self._resolve_source_directory(source_dir)
        unsupported_root = self._resolve_quarantine_root(unsupported_dir)
        failed_root = self._resolve_quarantine_root(failed_dir)
        batch_name = source.name or "imports"
        unsupported_batch = unsupported_root / batch_name
        failed_batch = failed_root / batch_name
        counts = {
            "scanned": 0,
            "imported": 0,
            "unchanged": 0,
            "duplicate": 0,
            "unsupported": 0,
            "failed": 0,
        }
        unsupported_files: list[BatchFileResult] = []
        failed_files: list[BatchFileResult] = []

        for document in self._files(source):
            counts["scanned"] += 1
            relative = document.relative_to(source)
            if document.suffix.lower() not in SUPPORTED_SUFFIXES:
                counts["unsupported"] += 1
                copied = self._copy_to_quarantine(
                    document,
                    relative,
                    unsupported_batch,
                )
                if copied.error is None:
                    unsupported_files.append(copied)
                else:
                    counts["failed"] += 1
                    failed_files.append(copied)
                continue

            try:
                result = self.ingestion.ingest(
                    owner_id=owner_id,
                    knowledge_base=knowledge_base,
                    path=document,
                    request_id=None,
                )
            except Exception as exc:
                counts["failed"] += 1
                copied = self._copy_to_quarantine(document, relative, failed_batch)
                error = _public_ingestion_error(exc)
                if copied.error:
                    error = f"{error};{copied.error}"
                failed_files.append(
                    BatchFileResult(
                        source=str(document),
                        copied_to=copied.copied_to,
                        error=error,
                    )
                )
                continue

            if result.status == "unchanged":
                counts["unchanged"] += 1
            elif result.status == "duplicate":
                counts["duplicate"] += 1
            else:
                counts["imported"] += 1

        return BatchIngestResult(
            status=("completed_with_failures" if counts["failed"] else "completed"),
            source_directory=str(source),
            unsupported_directory=str(unsupported_batch),
            failed_directory=str(failed_batch),
            counts=counts,
            unsupported_files=unsupported_files,
            failed_files=failed_files,
        )

    def _resolve_source_directory(self, value: str | Path) -> Path:
        candidate = _absolute_lexical(Path(value).expanduser())
        if _contains_link_component(candidate):
            raise BatchIngestionError(
                "Batch source directory must not contain links or junctions"
            )
        try:
            source = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BatchIngestionError("Batch source directory does not exist") from exc
        if not source.is_dir():
            raise BatchIngestionError("Batch source path is not a directory")

        try:
            allowed_roots = [
                Path(root).expanduser().resolve(strict=True)
                for root in self.settings.allowed_roots
            ]
        except (OSError, RuntimeError) as exc:
            raise BatchIngestionError(
                "Configured imports root is unavailable"
            ) from exc
        if not any(_is_within(source, root) for root in allowed_roots):
            raise BatchIngestionError(
                "Batch source directory is outside configured imports roots"
            )
        return source

    @staticmethod
    def _resolve_quarantine_root(value: str | Path) -> Path:
        candidate = _absolute_lexical(Path(value).expanduser())
        if _contains_link_component(candidate):
            raise BatchIngestionError(
                "Quarantine root must not contain links or junctions"
            )
        return candidate

    @staticmethod
    def _files(source: Path) -> list[Path]:
        documents: list[Path] = []

        def scan_error(_error: OSError) -> None:
            raise BatchIngestionError("Batch source directory cannot be scanned")

        try:
            for current, directory_names, file_names in os.walk(
                source,
                followlinks=False,
                onerror=scan_error,
            ):
                current_path = Path(current)
                directory_names[:] = sorted(
                    name
                    for name in directory_names
                    if not _is_link(current_path / name)
                )
                for name in sorted(file_names):
                    candidate = current_path / name
                    if _is_link(candidate) or not _is_regular_file(candidate):
                        continue
                    documents.append(_absolute_lexical(candidate))
        except BatchIngestionError:
            raise
        except (OSError, RuntimeError) as exc:
            raise BatchIngestionError(
                "Batch source directory cannot be scanned"
            ) from exc
        return sorted(documents, key=lambda path: path.relative_to(source).as_posix())

    @staticmethod
    def _copy_to_quarantine(
        source: Path,
        relative: Path,
        batch_root: Path,
    ) -> BatchFileResult:
        destination = _absolute_lexical(batch_root / relative)
        try:
            quarantine_root = _absolute_lexical(batch_root.parent)
            batch_root = _absolute_lexical(batch_root)
            if not _is_within(batch_root, quarantine_root):
                raise BatchIngestionError("Unsafe quarantine batch path")

            if _supports_posix_no_follow():
                _copy_regular_no_follow(source, destination, quarantine_root)
                return BatchFileResult(
                    source=str(source),
                    copied_to=str(destination),
                )

            if _contains_link_component(quarantine_root) or _contains_link_component(
                batch_root
            ):
                raise BatchIngestionError("Unsafe quarantine link component")

            batch_root.mkdir(parents=True, exist_ok=True)
            if _contains_link_component(batch_root):
                raise BatchIngestionError("Unsafe quarantine link component")

            destination = _absolute_lexical(batch_root / relative)
            if not _is_within(destination, batch_root):
                raise BatchIngestionError("Unsafe quarantine destination")
            if _contains_link_component(destination.parent) or _is_link(destination):
                raise BatchIngestionError("Unsafe quarantine link component")

            destination.parent.mkdir(parents=True, exist_ok=True)
            if _contains_link_component(destination.parent) or _is_link(destination):
                raise BatchIngestionError("Unsafe quarantine link component")

            resolved_quarantine = quarantine_root.resolve(strict=True)
            resolved_batch = batch_root.resolve(strict=True)
            resolved_destination = destination.resolve(strict=False)
            if not _is_within(resolved_batch, resolved_quarantine) or not _is_within(
                resolved_destination,
                resolved_batch,
            ):
                raise BatchIngestionError("Unsafe quarantine destination")
            _copy_regular_no_follow(source, destination, quarantine_root)
        except (BatchIngestionError, OSError, RuntimeError):
            return BatchFileResult(
                source=str(source),
                copied_to=str(destination),
                error="QUARANTINE_COPY_FAILED",
            )
        return BatchFileResult(source=str(source), copied_to=str(destination))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _contains_link_component(path: Path) -> bool:
    absolute = _absolute_lexical(path)
    components = [absolute, *absolute.parents]
    return any(_is_link(component) for component in components)


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError as exc:
        raise BatchIngestionError(
            "Batch source directory cannot be scanned"
        ) from exc


def _copy_regular_no_follow(
    source: Path,
    destination: Path,
    quarantine_root: Path,
) -> None:
    if _supports_posix_no_follow():
        _copy_posix_no_follow(source, destination, quarantine_root)
        return

    if (
        _contains_link_component(source)
        or not _is_regular_file(source)
        or _contains_link_component(destination.parent)
        or _is_link(destination)
    ):
        raise BatchIngestionError("Unsafe quarantine copy path")
    with source.open("rb") as input_stream, destination.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)


def _supports_posix_no_follow() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


def _copy_posix_no_follow(
    source: Path,
    destination: Path,
    quarantine_root: Path,
) -> None:
    relative = destination.relative_to(quarantine_root)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise BatchIngestionError("Unsafe quarantine destination")

    source_parent_fd = _open_posix_directory(source.parent, create=False)
    source_fd: int | None = None
    destination_root_fd: int | None = None
    destination_parent_fd: int | None = None
    temporary_name: str | None = None
    try:
        source_fd = os.open(
            source.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=source_parent_fd,
        )
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise BatchIngestionError("Batch source entry is not a regular file")

        destination_root_fd = _open_posix_directory(
            quarantine_root,
            create=True,
        )
        destination_parent_fd = _open_posix_relative_directories(
            destination_root_fd,
            relative.parts[:-1],
            create=True,
        )
        temporary_name = f".knowledge-{secrets.token_hex(8)}.tmp"
        destination_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_parent_fd,
        )
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            ordinary_permissions = (
                stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
            )
            safe_mode = stat.S_IMODE(source_stat.st_mode) & ordinary_permissions
            os.fchmod(destination_fd, safe_mode)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        os.utime(
            temporary_name,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        os.replace(
            temporary_name,
            relative.name,
            src_dir_fd=destination_parent_fd,
            dst_dir_fd=destination_parent_fd,
        )
        temporary_name = None
    finally:
        if temporary_name is not None and destination_parent_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=destination_parent_fd)
            except OSError:
                pass
        for descriptor in (
            destination_parent_fd,
            destination_root_fd,
            source_fd,
            source_parent_fd,
        ):
            if descriptor is not None:
                os.close(descriptor)


def _open_posix_directory(path: Path, *, create: bool) -> int:
    absolute = _absolute_lexical(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = _open_posix_directory_at(
                descriptor,
                component,
                flags=flags,
                create=create,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_posix_relative_directories(
    root_fd: int,
    components: tuple[str, ...],
    *,
    create: bool,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.dup(root_fd)
    try:
        for component in components:
            if component in {"", ".", ".."}:
                raise BatchIngestionError("Unsafe quarantine destination")
            next_descriptor = _open_posix_directory_at(
                descriptor,
                component,
                flags=flags,
                create=create,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_posix_directory_at(
    parent_fd: int,
    component: str,
    *,
    flags: int,
    create: bool,
) -> int:
    if create:
        try:
            os.mkdir(component, mode=0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    return os.open(component, flags, dir_fd=parent_fd)


def _public_ingestion_error(error: Exception) -> str:
    if isinstance(error, IngestionDeniedError):
        return "INGESTION_DENIED"
    if isinstance(error, KnowledgeUnavailableError):
        return "KNOWLEDGE_UNAVAILABLE"
    if isinstance(error, UnsupportedDocumentError):
        return "UNSUPPORTED_DOCUMENT"
    if isinstance(error, ValueError):
        return "INVALID_DOCUMENT"
    return "INGESTION_FAILED"
