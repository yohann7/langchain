# XiaoXu Token Usage Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every daily Token limit and retain accurate per-user, per-UTC-day usage statistics for Agent and summarization model calls.

**Architecture:** Keep `DailyUsageStore` and the existing SQLite schema as the single statistics store. Strip budget decisions from `ToolExecutionGateway` and `ModelUsageMiddleware`, add an audit `purpose`, and attach a metadata-filtered callback to the summarization model so summary calls are counted without double-counting normal Agent calls.

**Tech Stack:** Python 3.13, LangChain/LangGraph middleware, `langchain_core` callbacks, SQLite, Pydantic Settings, pytest.

## Global Constraints

- Modify only `D:\Code\Python\langchain\Demo\XiaoXu`.
- Use `D:\Anaconda3\envs\langchain1.2\python.exe` for every test.
- Preserve `D:\Code\Python\langchain\Demo\XiaoXu\.private_agent\xiaoxu.db` and all existing `daily_usage` rows.
- Remove `daily_token_budget` completely; do not leave a deprecated runtime field or compatibility output.
- Keep `max_model_calls_per_run` and `max_tool_calls_per_run` unchanged.
- Do not modify Knowledge, Milvus, SearXNG, WxBot, long-term memory, or checkpoint semantics.
- The workspace is not a Git repository; skip commit steps and report that no commit was created.

---

### Task 1: Remove all Token-limit execution branches

**Files:**
- Modify: `tests/private_agent/test_governance.py`
- Modify: `src/private_agent/agent/governance.py`
- Modify: `src/private_agent/agent/factory.py`
- Modify: `src/private_agent/core/settings.py`

**Interfaces:**
- Consumes: `DailyUsageStore.record(user_id=..., ...)` and current permission/audit gateway behavior.
- Produces: `ToolExecutionGateway(..., default_user_id: str)` with no budget parameter; `record_model_usage(..., purpose: str = "agent")` remains the statistics entry point.

- [ ] **Step 1: Replace the budget-rejection test with a failing no-limit behavior test**

In `tests/private_agent/test_governance.py`, remove the `DailyTokenBudgetExceeded` import and replace `test_daily_budget_is_checked_before_model_or_tool_execution` with:

```python
def test_former_daily_limit_never_blocks_model_or_tool_execution(tmp_path):
    settings = _skill_settings(tmp_path)
    runtime = RuntimeState()
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "activate_skill",
                    "args": {"name": "example"},
                    "id": "over-old-limit",
                    "type": "tool_call",
                }],
                usage_metadata=_usage(10, 2),
            ),
            AIMessage(content="done", usage_metadata=_usage(4, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings, PermissionPolicy(), runtime, model=model
    )
    resources.usage.record(user_id=settings.user_id, input_tokens=100_001)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "activate"}]},
        config={"configurable": {"thread_id": "usage-only"}},
    )

    assert result["messages"][-1].content == "done"
    assert resources.gateway.daily_usage().total_tokens == 100_018
    assert resources.gateway.daily_usage().tool_calls == 1
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_governance.py::test_former_daily_limit_never_blocks_model_or_tool_execution -q
```

Expected: FAIL because the current model precheck raises `DailyTokenBudgetExceeded` before the fake model executes.

- [ ] **Step 3: Remove budget production code**

In `src/private_agent/agent/governance.py`:

- Delete `DailyTokenBudgetExceeded`.
- Delete the `daily_token_budget` constructor argument and attribute.
- Delete `ensure_budget()`.
- Delete the budget branches in `execute`, `wrap_tool_call`, and `awrap_tool_call`.
- Delete model-call budget prechecks from sync and async `ModelUsageMiddleware`.
- Add `purpose: str = "agent"` to `record_model_usage` and include it in the audit payload.

The resulting audit payload construction must contain:

```python
payload: dict[str, Any] = {
    "purpose": purpose,
    "status": status,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "daily_total_tokens": usage.total_tokens,
}
```

In `src/private_agent/agent/factory.py`, stop passing `daily_token_budget` to `ToolExecutionGateway`.

In `src/private_agent/core/settings.py`, delete the `daily_token_budget` field.

- [ ] **Step 4: Run governance tests and verify GREEN**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_governance.py -q
```

Expected: all tests in the file pass.

---

### Task 2: Make `/usage` a statistics-only contract

**Files:**
- Modify: `tests/private_agent/test_commands.py`
- Modify: `src/private_agent/persistence/usage.py`
- Modify: `src/private_agent/commands.py`

**Interfaces:**
- Consumes: `ToolExecutionGateway.daily_usage() -> DailyUsage`.
- Produces: `DailyUsage.usage_date: str`; `DailyUsage.to_dict()` continues returning only five numeric usage metrics; `/usage` returns `{"usage_date": str, "usage": dict}`.

- [ ] **Step 1: Add a failing `/usage` contract test**

Add to `tests/private_agent/test_commands.py`:

```python
import json


def test_usage_reports_statistics_without_any_limit(tmp_path):
    settings = AppSettings(run_dir=tmp_path, enable_summarization_middleware=False)
    runtime = RuntimeState()
    resources = create_resources(settings, PermissionPolicy(), runtime)
    resources.usage.record(
        user_id=settings.user_id,
        model_calls=2,
        tool_calls=3,
        input_tokens=120_000,
        output_tokens=4_000,
        usage_date="2026-08-09",
    )

    response = handle_command(
        "/usage", runtime, settings, PermissionPolicy(), resources.registry
    )
    payload = json.loads(response.message)

    assert payload == {
        "usage_date": resources.gateway.daily_usage().usage_date,
        "usage": {
            "model_calls": 2,
            "tool_calls": 3,
            "input_tokens": 120_000,
            "output_tokens": 4_000,
            "total_tokens": 124_000,
        },
    }
```

Use the current UTC date in the fixture rather than a stale literal if the test execution date differs; derive it independently with `datetime.now(timezone.utc).date().isoformat()`.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_commands.py::test_usage_reports_statistics_without_any_limit -q
```

Expected: FAIL because `DailyUsage` has no `usage_date` and `/usage` still emits budget fields.

- [ ] **Step 3: Implement dated statistics output**

Change `DailyUsage` to carry `usage_date` while preserving its numeric dictionary contract:

```python
@dataclass(frozen=True)
class DailyUsage:
    usage_date: str
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
```

`DailyUsageStore.load()` must return `DailyUsage(usage_date=selected_date, ...)` both for empty and existing rows.

Replace the budget block in `_usage()` with:

```python
return CommandResponse(
    True,
    json.dumps(
        {"usage_date": usage_record.usage_date, "usage": usage_record.to_dict()},
        ensure_ascii=False,
        indent=2,
    ),
)
```

When no registry gateway is present, construct a `DailyUsage` from the runtime counters using the current UTC date.

- [ ] **Step 4: Run command and persistence-related tests**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_commands.py tests/private_agent/test_governance.py tests/private_agent/test_runtime.py -q
```

Expected: all selected tests pass.

---

### Task 3: Count summarization-model Token usage exactly once

**Files:**
- Create: `src/private_agent/agent/usage_callback.py`
- Modify: `src/private_agent/agent/factory.py`
- Modify: `tests/private_agent/test_governance.py`

**Interfaces:**
- Consumes: `ToolExecutionGateway.record_model_usage(input_tokens, output_tokens, purpose=..., status=..., error_type=...)`.
- Produces: `SummaryUsageCallback(record_usage: Callable[..., None])`; callback tracks only runs whose start metadata has `lc_source="summarization"`.

- [ ] **Step 1: Add a failing real summarization accounting test**

Add to `tests/private_agent/test_governance.py`:

```python
def test_summarization_and_agent_calls_are_each_counted_once(tmp_path):
    settings = AppSettings(
        run_dir=tmp_path,
        enable_pii_middleware=False,
        enable_summarization_middleware=True,
        summarization_trigger_tokens=20,
        summarization_keep_tokens=5,
    )
    model = ToolBindableFakeMessagesChatModel(
        responses=[
            AIMessage(content="first", usage_metadata=_usage(3, 1)),
            AIMessage(content="summary", usage_metadata=_usage(7, 2)),
            AIMessage(content="second", usage_metadata=_usage(4, 1)),
        ]
    )
    agent, resources = create_private_agent(
        settings, PermissionPolicy(), RuntimeState(), model=model
    )
    config = {"configurable": {"thread_id": "summary-accounting"}}

    agent.invoke(
        {"messages": [{"role": "user", "content": "A" * 200}]}, config=config
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "continue"}]}, config=config
    )

    usage = resources.gateway.daily_usage()
    assert usage.model_calls == 3
    assert usage.input_tokens == 14
    assert usage.output_tokens == 4
    with resources.database.connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM audit_events "
            "WHERE event_type='model_usage_recorded' ORDER BY created_at"
        ).fetchall()
    assert [json.loads(row["payload"])["purpose"] for row in rows] == [
        "agent",
        "summarization",
        "agent",
    ]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_governance.py::test_summarization_and_agent_calls_are_each_counted_once -q
```

Expected: FAIL with two recorded calls because the existing summary invocation bypasses `ModelUsageMiddleware`.

- [ ] **Step 3: Implement the metadata-filtered summary callback**

Create `src/private_agent/agent/usage_callback.py` with a `BaseCallbackHandler` that:

- On `on_chat_model_start`, stores an approximate input count keyed by `run_id` only when `metadata.get("lc_source") == "summarization"`.
- On `on_llm_end`, ignores unknown run IDs; otherwise sums `AIMessage.usage_metadata` from `response.generations`, falling back to approximate output counting, then calls `record_usage(..., purpose="summarization")`.
- On `on_llm_error`, ignores unknown run IDs; otherwise records the stored input estimate, zero output, `status="error"`, and `error_type=type(error).__name__`.
- Protects the in-flight run dictionary with `threading.Lock` so concurrent API requests cannot corrupt callback state.

Expose:

```python
def attach_summary_usage_callback(
    model: BaseChatModel,
    record_usage: Callable[..., None],
) -> SummaryUsageCallback:
    ...
```

The function appends the handler to `model.callbacks` without removing existing callbacks and returns the handler for test visibility.

In `build_middleware`, construct `SummarizationMiddleware`, attach the callback to `summary.model`, and then append the middleware.

- [ ] **Step 4: Run governance tests and verify GREEN**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest tests/private_agent/test_governance.py -q
```

Expected: all governance tests pass, with three usage rows in the summary test.

---

### Task 4: Remove configuration and documentation residue

**Files:**
- Modify: `config/defaults.yaml`
- Modify: `.env.example`
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/legacy/v1-plan.md`

**Interfaces:**
- Consumes: the completed statistics-only runtime contract.
- Produces: project documentation and examples with no active Token-limit setting or behavior claim.

- [ ] **Step 1: Remove obsolete declarations and descriptions**

- Delete `daily_token_budget: 100000` from `config/defaults.yaml`.
- Delete `PRIVATE_AGENT_DAILY_TOKEN_BUDGET=100000` from `.env.example`.
- Replace architecture text claiming a pre-execution Token budget check with text stating that model/tool usage is recorded per user and UTC date without an execution limit.
- Delete the active configuration description of `daily_token_budget`.
- Update the legacy `/usage` description so it describes counters only and is not mistaken for current quota behavior.

- [ ] **Step 2: Verify configuration loading no longer exposes the setting**

Run:

```powershell
$env:PYTHONPATH='D:\Code\Python\langchain\Demo\XiaoXu\src'
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -c "from private_agent.config import AppSettings; assert not hasattr(AppSettings(), 'daily_token_budget')"
```

Expected: exit code 0.

- [ ] **Step 3: Scan runtime source, active config, examples, and active docs**

Run:

```powershell
rg -n "DailyTokenBudgetExceeded|ensure_budget|daily_token_budget|remaining_daily_budget|budget exhausted|Token 预算已用尽" src config .env.example docs/architecture.md docs/configuration.md
```

Expected: no matches. Matches in the approved design and implementation plan are allowed because they document removal requirements.

---

### Task 5: Full verification

**Files:**
- Verify only; do not modify unrelated projects.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: fresh evidence for behavior, test coverage, syntax, and residue removal.

- [ ] **Step 1: Run the full XiaoXu test suite**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m pytest 'D:\Code\Python\langchain\Demo\XiaoXu\tests' -q
```

Expected: exit code 0 and zero failed tests.

- [ ] **Step 2: Compile all Python source**

Run:

```powershell
& 'D:\Anaconda3\envs\langchain1.2\python.exe' -m compileall -q 'D:\Code\Python\langchain\Demo\XiaoXu\src'
```

Expected: exit code 0.

- [ ] **Step 3: Run final residue and live-data safety checks**

Run the Task 4 residue scan again, then verify that `.private_agent/xiaoxu.db` still exists and its `daily_usage` row count is unchanged or greater than the pre-implementation count. Do not update, delete, or vacuum the live database.

- [ ] **Step 4: Report implementation evidence**

Report changed files, red/green test evidence, full test count, compile result, residue-scan result, preserved database path, and the absence of a Git commit because the workspace is not a repository.
