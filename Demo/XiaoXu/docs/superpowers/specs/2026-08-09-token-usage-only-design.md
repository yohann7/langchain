# XiaoXu 纯 Token 用量统计设计

## 目标

彻底删除 XiaoXu 的每日 Token 限额功能。模型调用和工具调用不得再因历史 Token 用量被拒绝；系统只按用户、按 UTC 日期持久化模型调用次数、工具调用次数、输入 Token、输出 Token 和总 Token。

正式 Agent 回答与上下文摘要都属于模型消耗，必须进入同一统计和审计边界，并通过 `purpose=agent|summarization` 区分用途。

## 范围

本次只修改 `D:\Code\Python\langchain\Demo\XiaoXu`。

包含：

- 删除 `daily_token_budget` 设置、示例配置和文档说明。
- 删除 `DailyTokenBudgetExceeded`、`ensure_budget()` 及模型、同步工具、异步工具和直接工具执行中的额度阻断。
- 保留 `daily_usage` SQLite 表、已有行和按用户/UTC 日期隔离语义。
- 保留每轮模型调用数和工具调用数的现有运行时限制；这些限制用于防止单轮循环，不属于 Token 额度。
- `/usage` 只显示统计日期和用量，不再显示限额或剩余额度。
- 正式模型和摘要模型均记录用量；当前二者实际复用同一个 `deepseek.deepseek-v4-flash`，但统计用途不同。
- 更新测试和人类文档。

不包含：

- 不删除或重建 `xiaoxu.db`。
- 不修改长期记忆、Knowledge、Milvus、SearXNG 或 WxBot。
- 不增加费用换算、月报、累计报表或新的管理界面。
- 不移除 `max_model_calls_per_run` 和 `max_tool_calls_per_run`。

## 架构

### 用量存储

`DailyUsageStore` 继续作为唯一持久化边界，使用 `(user_id, usage_date)` 主键累计：

- `model_calls`
- `tool_calls`
- `input_tokens`
- `output_tokens`

`DailyUsage` 增加或暴露当前 `usage_date`，使 `/usage` 能明确说明统计周期。历史 schema 不变，不需要 migration。

### 工具执行

`ToolExecutionGateway` 继续负责权限、审批、执行审计和工具调用计数，但不再拥有预算字段或预算判断。只有真正进入后端的工具调用才增加 `tool_calls`；权限拒绝、审批未完成、搜索策略重复或超限仍不增加实际工具计数。

### 正式模型统计

`ModelUsageMiddleware` 不再在调用前进行预算预检。调用成功后优先采用模型返回的 `usage_metadata`；调用异常时保留当前的输入估算并记录 `output_tokens=0`。审计事件 `model_usage_recorded` 增加 `purpose="agent"`。

### 摘要模型统计

摘要模型继续复用当前已选模型，除非用户显式配置独立摘要模型。摘要调用通过模型回调记录用量。回调只跟踪 LangChain 已标记 `metadata.lc_source="summarization"` 的运行；同一模型执行正式 Agent 调用时由 `ModelUsageMiddleware` 统计，摘要回调忽略该运行，从而避免重复计数并保持当前主模型/摘要模型共用实例的行为。

摘要统计写入同一个 `DailyUsageStore` 和 `model_usage_recorded` 审计事件，并使用 `purpose="summarization"`。摘要调用失败时记录可获得的输入估算和 `output_tokens=0`，但本设计不改变 LangChain 摘要失败后的上下文处理语义；该问题应由独立的上下文治理改造处理。

### `/usage` 输出

输出契约改为：

```json
{
  "usage_date": "2026-08-09",
  "usage": {
    "model_calls": 12,
    "tool_calls": 8,
    "input_tokens": 123456,
    "output_tokens": 7890,
    "total_tokens": 131346
  }
}
```

不再出现 `daily_token_budget`、`remaining_daily_budget_estimate`、`budget exhausted` 或任何同义字段和错误。

## 数据流

正式回答：

```text
用户输入 -> Agent 模型 -> ModelUsageMiddleware -> DailyUsageStore + audit_events
```

上下文摘要：

```text
SummarizationMiddleware -> 摘要专用用量回调 -> DailyUsageStore + audit_events
```

工具调用：

```text
权限/审批/搜索策略 -> 真正执行工具 -> tool_calls + 1 -> 审计
```

任何路径都不再读取历史 Token 总量来决定是否允许执行。

## 错误处理

- 模型或摘要调用失败仍记录一次模型调用，输入使用可获得的真实值或调用前估算值，输出记为零，并在审计中保存 `status=error` 和 `error_type`。
- 用量统计写入失败沿用当前 fail-closed 行为，不静默伪造统计成功。
- 旧 `.env` 中残留的 `PRIVATE_AGENT_DAILY_TOKEN_BUDGET` 因 Pydantic `extra="ignore"` 被忽略；项目配置、示例和文档不再声明该变量。
- 旧数据库中的 `daily_usage` 数据继续可读并在同一日期行上累计。

## 测试设计

1. 预置超过 100,000 Token 的历史用量后，正式模型仍执行并继续累计。
2. 预置超过 100,000 Token 的历史用量后，同步、异步和直接工具调用仍执行并计数。
3. 权限拒绝和策略阻止仍不计为真实工具调用。
4. 正式模型成功与失败分别记录 `purpose=agent`。
5. 强制触发一次摘要，验证其调用被计入 `daily_usage`，审计为 `purpose=summarization`，且正式模型不重复计数。
6. `/usage` 返回 `usage_date` 和五项用量字段，不返回任何预算字段。
7. 设置模型不再暴露 `daily_token_budget`；默认 YAML 和 `.env.example` 不再声明它。
8. 运行 XiaoXu 全量测试，并扫描源代码、配置和文档，确认没有预算阻断符号或用户可见额度语义残留。

## 验收标准

- 任意历史 Token 总量都不会阻止模型或工具执行。
- 每次正式模型和摘要模型调用只统计一次。
- 按用户、按 UTC 日期隔离保持不变。
- `/usage` 仅报告用量。
- 既有 SQLite 用量数据完整保留。
- XiaoXu 全量测试通过。

## 仓库说明

当前 `D:\Code\Python\langchain` 及 `Demo\XiaoXu` 未被 Git 识别为仓库，因此本设计文件只能写入工作区，无法创建设计提交。
