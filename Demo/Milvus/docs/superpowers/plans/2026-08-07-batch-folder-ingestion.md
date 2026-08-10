# Knowledge Service Batch Folder Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recursive `knowledge-admin ingest-folder` command that imports supported files and copies unsupported or failed files into separate writable quarantine trees.

**Architecture:** A new `BatchIngestionService` owns directory validation, deterministic traversal, file classification, quarantine copies, and batch summaries while delegating every supported document to the existing transactional `IngestionService.ingest()`. The CLI exposes the batch service with zero-argument defaults beyond the source path; HTTP API v1 remains unchanged.

**Tech Stack:** Python 3.13, argparse, pathlib/os/shutil, dataclasses, pytest, FastAPI composition root, Docker Compose.

## Global Constraints

- Only `knowledge-admin` gains folder ingestion; HTTP API v1 and `contracts/knowledge-api-v1.json` must not change.
- `/imports` remains read-only; `/unsupported` and `/failed` are writable quarantine roots.
- Defaults are exactly `local-user`, `personal`, `/unsupported`, and `/failed`.
- Traversal is recursive and deterministic, never follows symlinks, and keeps relative paths in quarantine.
- Source documents remain untouched; quarantine uses `copy2` and overwrites an existing same-path copy.
- Unsupported files do not cause exit code 1; ingestion or quarantine-copy failures do.
- The workspace has no Git metadata, so worktree creation and per-task commits are unavailable; execute in place and preserve unrelated files.

---

### Task 1: Batch ingestion service

**Files:**
- Create: `src/knowledge_service/batch_ingestion.py`
- Modify: `src/knowledge_service/api/app.py`
- Test: `tests/unit/test_batch_ingestion.py`

**Interfaces:**
- Consumes: `KnowledgeSettings.allowed_roots`, `SUPPORTED_SUFFIXES`, and `IngestionService.ingest(owner_id, knowledge_base, path, request_id=None)`.
- Produces: `BatchIngestionService.ingest_folder(...) -> BatchIngestResult`, `BatchIngestResult.has_failures`, and dataclass JSON-compatible fields used by the CLI.

- [ ] **Step 1: Write failing tests for recursive classification and continuation**

Create real temporary directory trees and a small recording ingestion double. Cover stable path order, `active`/`unchanged`/`duplicate`, unsupported copying, failed copying, preserved source files, and continuation:

```python
def test_ingest_folder_recurses_classifies_and_continues(tmp_path):
    source = tmp_path / "imports" / "project-a"
    (source / "nested").mkdir(parents=True)
    (source / "good.txt").write_text("good", encoding="utf-8")
    (source / "nested" / "same.md").write_text("same", encoding="utf-8")
    (source / "nested" / "bad.pdf").write_bytes(b"broken")
    (source / "tool.exe").write_bytes(b"binary")

    ingestion = RecordingIngestion(fail_names={"bad.pdf"})
    service = BatchIngestionService(settings_for(tmp_path), ingestion)
    result = service.ingest_folder(
        owner_id="local-user",
        knowledge_base="personal",
        source_dir=source,
        unsupported_dir=tmp_path / "unsupported",
        failed_dir=tmp_path / "failed",
    )

    assert ingestion.paths == [source / "good.txt", source / "nested" / "bad.pdf", source / "nested" / "same.md"]
    assert (tmp_path / "unsupported" / "project-a" / "tool.exe").read_bytes() == b"binary"
    assert (tmp_path / "failed" / "project-a" / "nested" / "bad.pdf").read_bytes() == b"broken"
    assert (source / "tool.exe").exists()
    assert result.counts == {
        "scanned": 4, "imported": 1, "unchanged": 1,
        "duplicate": 0, "unsupported": 1, "failed": 1,
    }
    assert result.has_failures is True
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests\unit\test_batch_ingestion.py -q
```

Expected: collection fails because `knowledge_service.batch_ingestion` does not exist.

- [ ] **Step 3: Implement the minimal batch service**

Create frozen per-file/result dataclasses plus the orchestration service:

```python
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
    def __init__(self, settings: KnowledgeSettings, ingestion: IngestionService) -> None:
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
        ...
```

Use `os.walk(..., followlinks=False)`, remove symlink directories from `dirnames`, skip symlink files, sort relative paths, classify with `SUPPORTED_SUFFIXES`, delegate supported files, and use a bounded single-line error formatter. Validate every resolved source and destination against its authorized root before reading or copying.

- [ ] **Step 4: Compose the service without changing HTTP routes**

In `create_services()`, construct one `IngestionService`, pass it to `BatchIngestionService`, add `batch_ingestion` to `KnowledgeServices`, and keep the existing `ingestion` field:

```python
ingestion = IngestionService(settings, catalog, parser, embeddings, vectors)
batch_ingestion = BatchIngestionService(settings, ingestion)
```

- [ ] **Step 5: Run batch tests and verify GREEN**

Run the Task 1 command again. Expected: all batch tests pass.

---

### Task 2: CLI command and defaults

**Files:**
- Modify: `src/knowledge_service/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `services.batch_ingestion.ingest_folder(...)` and `BatchIngestResult.has_failures`.
- Produces: `knowledge-admin ingest-folder SOURCE_DIRECTORY` and defaulted single-file `ingest` arguments.

- [ ] **Step 1: Write failing CLI tests**

Add tests proving both default and override behavior and JSON/exit-code output:

```python
def test_ingest_defaults_to_local_user_and_personal(monkeypatch, capsys):
    result = cli.main(["ingest", "/imports/manual.pdf"])
    assert result == 0
    assert recorded == {
        "owner_id": "local-user",
        "knowledge_base": "personal",
        "path": "/imports/manual.pdf",
        "request_id": None,
    }


def test_ingest_folder_uses_defaults_and_returns_one_for_failed_batch(monkeypatch, capsys):
    result = cli.main(["ingest-folder", "/imports/project-a"])
    assert result == 1
    assert recorded["unsupported_dir"] == "/unsupported"
    assert recorded["failed_dir"] == "/failed"
    assert json.loads(capsys.readouterr().out)["status"] == "completed_with_failures"
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests\unit\test_cli.py -q
```

Expected: `ingest` rejects the missing user id and `ingest-folder` is not a known command.

- [ ] **Step 3: Implement argparse defaults and batch dispatch**

Change single-file arguments and add the command:

```python
ingest.add_argument("--user-id", default="local-user")
ingest.add_argument("--knowledge-base", default="personal")

ingest_folder = commands.add_parser("ingest-folder")
ingest_folder.add_argument("path")
ingest_folder.add_argument("--user-id", default="local-user")
ingest_folder.add_argument("--knowledge-base", default="personal")
ingest_folder.add_argument("--unsupported-dir", default="/unsupported")
ingest_folder.add_argument("--failed-dir", default="/failed")
```

Run the batch inside `services.coordinator.mutation()`, emit the dataclass through existing `_emit`, and return `1 if result.has_failures else 0`. Convert invalid source-directory errors to a single JSON error object and exit 1 without a traceback.

- [ ] **Step 4: Run CLI tests and verify GREEN**

Run the Task 2 command again. Expected: all CLI tests pass.

---

### Task 3: Compose mounts and repository directory layout

**Files:**
- Modify: `compose.yaml`
- Modify: `.gitignore`
- Modify: `scripts/validate_architecture.py`
- Modify: `tests/unit/test_packaging.py`
- Modify: `imports/README.md`
- Create: `imports/imports/.gitkeep`
- Create: `imports/unsupported/.gitkeep`
- Create: `imports/failed/.gitkeep`

**Interfaces:**
- Consumes: the batch command defaults `/imports`, `/unsupported`, and `/failed`.
- Produces: one read-only source mount and two writable quarantine mounts.

- [ ] **Step 1: Write failing packaging assertions**

Update the parsed Compose test to require exactly these entries and reject the legacy mount:

```python
assert "./imports/imports:/imports:ro" in knowledge["volumes"]
assert "./imports/unsupported:/unsupported" in knowledge["volumes"]
assert "./imports/failed:/failed" in knowledge["volumes"]
assert "./imports:/imports:ro" not in knowledge["volumes"]
```

- [ ] **Step 2: Run packaging and architecture checks and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests\unit\test_packaging.py -q
& 'D:\Anaconda3\envs\langchain1.2\python.exe' scripts\validate_architecture.py
```

Expected: both checks report the old `./imports:/imports:ro` layout.

- [ ] **Step 3: Apply the mount and directory changes**

Set Compose volumes to:

```yaml
- ./imports/imports:/imports:ro
- ./imports/unsupported:/unsupported
- ./imports/failed:/failed
- ./runtime/knowledge:/data
```

Update `.gitignore` so only the three nested `.gitkeep` files and the root README are retained, and update architecture validation to require the same mount modes. Document that source files remain in `imports/imports` and quarantines are copies.

- [ ] **Step 4: Run packaging, architecture, and Compose parsing checks**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests\unit\test_packaging.py -q
& 'D:\Anaconda3\envs\langchain1.2\python.exe' scripts\validate_architecture.py
& 'D:\DockerDesktop\resources\bin\docker.exe' compose config --quiet
```

Expected: all exit 0.

---

### Task 4: User documentation and contract guard

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Verify unchanged: `contracts/knowledge-api-v1.json`

**Interfaces:**
- Consumes: final CLI syntax and mount layout.
- Produces: copy-paste-ready single and folder ingestion instructions plus rebuild/recreate guidance.

- [ ] **Step 1: Document the exact commands and result directories**

Add these examples and explain defaults/overrides:

```powershell
docker compose exec -T knowledge knowledge-admin ingest /imports/manual.pdf
docker compose exec -T knowledge knowledge-admin ingest-folder /imports/project-a
```

Document host mappings `./imports/unsupported/project-a` and `./imports/failed/project-a`, source preservation, recursive traversal, and exit-code behavior.

- [ ] **Step 2: Verify the OpenAPI contract is unchanged**

Generate to a temporary path by importing `create_api().openapi()` and compare the normalized JSON with `contracts/knowledge-api-v1.json`; do not overwrite the contract. Expected: equality because no route/schema changed.

- [ ] **Step 3: Run the complete local suite**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest -q
& 'D:\Anaconda3\envs\langchain1.2\python.exe' scripts\validate_architecture.py
```

Expected: zero failures and `ARCHITECTURE_VALIDATION=ok`.

---

### Task 5: Image rebuild and live container verification

**Files:**
- Runtime-only verification; no additional source files.

**Interfaces:**
- Consumes: updated Dockerfile context and Compose mounts.
- Produces: a recreated healthy Knowledge container exposing the new CLI.

- [ ] **Step 1: Rebuild only the Knowledge image**

Run:

```powershell
& 'D:\DockerDesktop\resources\bin\docker.exe' compose build knowledge
```

Expected: image `milvus-knowledge` builds successfully; Milvus image is neither rebuilt nor pulled.

- [ ] **Step 2: Recreate only the Knowledge service**

Run:

```powershell
& 'D:\DockerDesktop\resources\bin\docker.exe' compose up -d --force-recreate knowledge
```

Expected: Milvus stays healthy, Knowledge is recreated with the three mounts and becomes healthy.

- [ ] **Step 3: Verify runtime state and CLI exposure**

Run `docker compose ps -a`, call `http://127.0.0.1:8080/health/ready`, inspect mounts, and run:

```powershell
& 'D:\DockerDesktop\resources\bin\docker.exe' compose exec -T knowledge knowledge-admin ingest-folder --help
```

Expected: readiness returns `{"status":"ready"}`, the source mount is read-only, both quarantine mounts are writable, and help lists all four optional overrides.
