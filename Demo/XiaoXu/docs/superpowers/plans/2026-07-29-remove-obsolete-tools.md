# Remove Obsolete XiaoXu Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete eleven obsolete Agent tools, their dedicated modules and tests, and irreversibly migrate `xiaoxu.db` away from the `todos` and `reminders` tables.

**Architecture:** Keep the existing LangChain tool registry and permission middleware, but reduce the registered capabilities to Skill activation, Web Search, and Knowledge Search. Remove file-only security/configuration surfaces and use an idempotent SQLite schema migration to drop obsolete personal-data tables while preserving all other Agent state.

**Tech Stack:** Python 3.13, LangChain 1.2, LangGraph, Pydantic Settings, SQLite, pytest.

## Global Constraints

- CLI tools must be exactly `activate_skill`, `web_search`, and `search_knowledge`.
- WeCom tools must be exactly `activate_skill` and `search_knowledge`.
- Delete `todos` and `reminders` tables and all rows without an automatic backup.
- Preserve checkpoint, `model_state`, `tool_grants`, `audit_events`, and `schema_metadata`.
- Do not change Knowledge Service, Milvus, WxBot, checkpoint, summarization, or memory behavior.
- Keep `docs/legacy/v1-plan.md` unchanged as historical documentation.
- This checkout has no `.git`; commit steps cannot be executed.

---

### Task 1: Specify the reduced public tool surface

**Files:**
- Modify: `tests/private_agent/test_agent_factory.py`
- Modify: `tests/contract/test_project_boundaries.py`

**Interfaces:**
- Consumes: `create_private_agent(..., tool_profile: ToolProfile)`
- Produces: exact registry-name contracts and deleted-module path contracts

- [ ] **Step 1: Write failing registry and module-absence tests**

Replace the broad CLI assertions with:

```python
assert resources.registry.names() == [
    "activate_skill",
    "search_knowledge",
    "web_search",
]
```

Replace the WeCom assertion with:

```python
assert resources.registry.names() == [
    "activate_skill",
    "search_knowledge",
]
```

Add a contract test asserting that the obsolete compatibility modules, concrete
tool packages, persistence stores, and `storage.py` do not exist.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -m pytest tests/private_agent/test_agent_factory.py::test_create_private_agent_loads_v1_tools tests/private_agent/test_agent_factory.py::test_wecom_chat_profile_exposes_only_low_risk_tools tests/contract/test_project_boundaries.py -q
```

Expected: failure because all eleven tools and their modules still exist.

### Task 2: Specify destructive SQLite migration

**Files:**
- Modify: `tests/unit/test_persistence_schema.py`
- Modify: `tests/unit/test_sqlite_runtime_persistence.py`

**Interfaces:**
- Consumes: `XiaoXuDatabase(path: str | Path)`
- Produces: schema version 3 without `todos` or `reminders`

- [ ] **Step 1: Write a failing new-database test**

Assert:

```python
assert {"model_state", "tool_grants", "audit_events"} <= tables
assert "todos" not in tables
assert "reminders" not in tables
```

- [ ] **Step 2: Replace the todo persistence test with a migration test**

Create an old database containing populated `todos`, `reminders`,
`model_state`, `tool_grants`, and `audit_events`; instantiate
`XiaoXuDatabase(path)` and assert:

```python
assert "todos" not in tables
assert "reminders" not in tables
assert preserved_model_state_payload == '{"active":"demo"}'
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -m pytest tests/unit/test_persistence_schema.py tests/unit/test_sqlite_runtime_persistence.py -q
```

Expected: failure because version 2 still creates both obsolete tables.

### Task 3: Remove Agent registrations and runtime resources

**Files:**
- Modify: `src/private_agent/agent/factory.py`
- Modify: `src/private_agent/agent/profiles.py`
- Modify: `src/private_agent/interfaces/api/app.py`

**Interfaces:**
- Consumes: existing `ToolRegistry`, `SkillLoader`, `KnowledgeClient`
- Produces: reduced `build_tools()` result and smaller `AgentResources`

- [ ] **Step 1: Remove obsolete imports and resource fields**

Delete imports for time, calculation, todo, reminder, file tools and stores.
Remove `todos`, `reminders`, and `allowed_roots` from `AgentResources` and
`create_resources()`.

- [ ] **Step 2: Remove eleven LangChain tool wrappers**

Keep only:

```python
activate_skill_tool
web_search_tool
search_knowledge_tool
```

Set `all_tools` to those three values.

- [ ] **Step 3: Reduce the WeCom allowlist**

Set:

```python
"wecom_chat": frozenset({"activate_skill", "search_knowledge"})
```

Remove `PUBLIC_WECOM_TOOLS`; `_public_tool_status()` should return no public
status for arbitrary tool names while existing Knowledge result handling stays
unchanged.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run the exact Task 1 pytest command. Expected: registry assertions pass; module
absence still fails until Task 5.

### Task 4: Remove obsolete configuration and file-security surface

**Files:**
- Modify: `src/private_agent/core/settings.py`
- Modify: `src/private_agent/interfaces/cli/app.py`
- Modify: `src/private_agent/security.py`
- Modify: `src/private_agent/commands.py`
- Modify: `tests/private_agent/test_config.py`
- Modify: `tests/private_agent/test_security.py`
- Modify: `tests/private_agent/test_commands.py`

**Interfaces:**
- Consumes: `PermissionPolicy(overrides=...)`
- Produces: generic risk/approval policy without filesystem fields

- [ ] **Step 1: Write failing absence and output tests**

Assert `AppSettings` lacks `todo_store_path`, `reminder_store_path`, and
`allowed_roots`; assert `ToolPermission` lacks `can_read_files` and
`can_write_files`; assert `/tools` output does not contain `read_files=` or
`write_files=`.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -m pytest tests/private_agent/test_config.py tests/private_agent/test_security.py tests/private_agent/test_commands.py -q
```

Expected: absence assertions fail on current classes.

- [ ] **Step 3: Remove configuration and security fields**

Delete obsolete settings and `normalized_allowed_roots()`. Construct CLI policy
with:

```python
PermissionPolicy(overrides=effective_permission_overrides(settings))
```

Delete path fields and `is_path_allowed()` from security objects, and remove
file capability columns from `/tools`.

- [ ] **Step 4: Run targeted tests and verify GREEN**

Run the exact Task 4 pytest command. Expected: all pass.

### Task 5: Apply database migration and delete modules/tests

**Files:**
- Modify: `src/private_agent/persistence/database.py`
- Delete: `src/private_agent/persistence/todos.py`
- Delete: `src/private_agent/persistence/reminders.py`
- Delete: `src/private_agent/storage.py`
- Delete: obsolete modules under `src/private_agent/tools/`
- Delete: `tests/private_agent/test_math_tools.py`
- Delete: `tests/private_agent/test_file_tools.py`
- Delete: `tests/private_agent/test_storage_tools.py`
- Modify: tests containing obsolete tool names as generic examples

**Interfaces:**
- Consumes: SQLite database at `XiaoXuDatabase.path`
- Produces: idempotent schema version 3 migration

- [ ] **Step 1: Implement schema version 3**

Set:

```python
SCHEMA_VERSION = 3
```

Remove obsolete `CREATE TABLE` statements and execute:

```sql
DROP TABLE IF EXISTS todos;
DROP TABLE IF EXISTS reminders;
```

inside `_initialize()` before updating `schema_metadata`.

- [ ] **Step 2: Run Task 2 tests and verify GREEN**

Run the exact Task 2 pytest command. Expected: obsolete tables are absent and
other state remains.

- [ ] **Step 3: Delete dedicated production modules and tests**

Delete all files listed in the design specification. Remove empty
`tools/files`, `tools/personal`, and `tools/utility` packages.

- [ ] **Step 4: Replace stale example names**

Use `search_knowledge`, `web_search`, or `demo_tool` in generic streaming,
approval, permission-grant, and CLI tests so the deleted names no longer imply
real capabilities.

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

Run the exact Task 1 pytest command. Expected: both reduced registries and
deleted-module contracts pass.

### Task 6: Full verification and residue audit

**Files:**
- Verify: all XiaoXu source, current docs, and tests

**Interfaces:**
- Consumes: completed deletion
- Produces: evidence that the reduced Agent is importable and tested

- [ ] **Step 1: Run all tests**

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -m pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Compile source**

```powershell
D:\Anaconda3\envs\langchain1.2\python.exe -m compileall -q src
```

Expected: exit code 0.

- [ ] **Step 3: Audit obsolete names**

Search source, current docs, and tests for all eleven names. Exclude
`docs/legacy/v1-plan.md`; expected result is empty.

- [ ] **Step 4: Inspect final runtime tool names**

Construct CLI and WeCom agents with the fake test model and confirm the exact
registry names required by the global constraints.

