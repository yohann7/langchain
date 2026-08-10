"""Administrative command line interface for the Knowledge Service."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from typing import Sequence

from knowledge_service.api.app import create_services
from knowledge_service.batch_ingestion import BatchIngestionError
from knowledge_service.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-admin")
    parser.add_argument("--config", default="config/knowledge.yaml")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="show owner-scoped service status")
    status.add_argument("--user-id", required=True)

    ingest = commands.add_parser("ingest", help="ingest one file from an allowed root")
    ingest.add_argument("path")
    ingest.add_argument("--user-id", default="local-user")
    ingest.add_argument("--knowledge-base", default="personal")
    ingest.add_argument("--request-id")

    ingest_folder = commands.add_parser(
        "ingest-folder",
        help="recursively ingest supported files from an allowed folder",
    )
    ingest_folder.add_argument("path")
    ingest_folder.add_argument("--user-id", default="local-user")
    ingest_folder.add_argument("--knowledge-base", default="personal")
    ingest_folder.add_argument("--unsupported-dir", default="/unsupported")
    ingest_folder.add_argument("--failed-dir", default="/failed")

    rebuild = commands.add_parser("rebuild", help="rebuild Milvus from managed documents")
    rebuild.set_defaults(command="rebuild")

    export = commands.add_parser("export", help="export SQLite and managed documents")
    export.add_argument("path")

    restore = commands.add_parser(
        "restore", help="restore an archive and synchronously rebuild Milvus"
    )
    restore.add_argument("path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    services = create_services(load_settings(args.config))
    exit_code = 0
    try:
        if args.command == "status":
            with services.coordinator.read():
                result = services.management.status(owner_id=args.user_id)
        elif args.command == "ingest":
            with services.coordinator.mutation():
                result = services.ingestion.ingest(
                    owner_id=args.user_id,
                    knowledge_base=args.knowledge_base,
                    path=args.path,
                    request_id=args.request_id,
                )
        elif args.command == "ingest-folder":
            try:
                with services.coordinator.mutation():
                    result = services.batch_ingestion.ingest_folder(
                        owner_id=args.user_id,
                        knowledge_base=args.knowledge_base,
                        source_dir=args.path,
                        unsupported_dir=args.unsupported_dir,
                        failed_dir=args.failed_dir,
                    )
            except BatchIngestionError as exc:
                result = {"status": "failed", "error": str(exc)}
                exit_code = 1
            else:
                exit_code = 1 if result.has_failures else 0
        elif args.command == "rebuild":
            with services.coordinator.maintenance():
                result = services.management.rebuild_index()
        elif args.command == "export":
            with services.coordinator.maintenance():
                result = services.archive.export_to(args.path)
        elif args.command == "restore":
            with services.coordinator.maintenance():
                restored = services.archive.restore_from(args.path)
                rebuilt = services.management.rebuild_index()
                result = {"restore": restored, "rebuild": rebuilt}
        else:  # pragma: no cover - argparse rejects unknown commands
            raise AssertionError(f"unsupported command: {args.command}")
        _emit(result)
        return exit_code
    finally:
        close = getattr(getattr(services, "vectors", None), "close", None)
        if callable(close):
            close()


def _emit(value: object) -> None:
    payload = asdict(value) if is_dataclass(value) else value
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
