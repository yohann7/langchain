"""Interactive menu handlers for model and thinking commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from private_agent.models import ModelManager, ModelOperationResult

AskFunc = Callable[[str, list[str] | None, str | None], str]


@dataclass(frozen=True)
class MenuResult:
    """Result returned by an interactive command menu."""

    message: str
    requires_agent_rebuild: bool = False


MODEL_MENU_OPTIONS = "\n".join(
    [
        "模型管理",
        "1. 查看全部厂家",
        "2. 查看全部模型",
        "3. 按厂家查看模型",
        "4. 查看模型详情",
        "5. 切换当前模型",
        "6. 查看模型配置状态",
        "0. 返回",
    ]
)

THINKING_MENU_OPTIONS = "\n".join(
    [
        "思考模式",
        "1. 开启思考模式",
        "2. 关闭思考模式",
        "3. 设置 reasoning_effort",
        "4. 恢复模型默认思考配置",
        "0. 返回",
    ]
)


def run_model_menu(manager: ModelManager, ask: AskFunc) -> MenuResult:
    """Run one model menu action."""

    choice = ask(_menu_prompt(manager, MODEL_MENU_OPTIONS), [str(index) for index in range(7)], "0")
    if choice == "0":
        return MenuResult("已返回。")
    if choice == "1":
        return MenuResult(manager.format_vendors())
    if choice == "2":
        return MenuResult(manager.format_all_models())
    if choice == "3":
        vendor = _choose_vendor(manager, ask)
        if vendor is None:
            return MenuResult("已返回。")
        return MenuResult(manager.format_models_for_vendor(vendor))
    if choice == "4":
        model_ref = _choose_model(manager, ask)
        if model_ref is None:
            return MenuResult("已返回。")
        return MenuResult(manager.format_profile(model_ref))
    if choice == "5":
        model_ref = _choose_model(manager, ask)
        if model_ref is None:
            return MenuResult("已返回。")
        return _operation_to_menu_result(manager.switch_model(model_ref))
    if choice == "6":
        return MenuResult(manager.format_config_status())
    return MenuResult("未知选项。")


def run_thinking_menu(manager: ModelManager, ask: AskFunc) -> MenuResult:
    """Run one thinking menu action."""

    choice = ask(_menu_prompt(manager, THINKING_MENU_OPTIONS), [str(index) for index in range(5)], "0")
    if choice == "0":
        return MenuResult("已返回。")
    if choice == "1":
        return _operation_to_menu_result(manager.set_thinking_enabled(True))
    if choice == "2":
        return _operation_to_menu_result(manager.set_thinking_enabled(False))
    if choice == "3":
        return _set_reasoning_effort(manager, ask)
    if choice == "4":
        return _operation_to_menu_result(manager.reset_thinking())
    return MenuResult("未知选项。")


def _menu_prompt(manager: ModelManager, menu_options: str) -> str:
    return "\n\n".join([manager.format_current(), menu_options])


def _choose_vendor(manager: ModelManager, ask: AskFunc) -> str | None:
    vendors = manager.vendors()
    rows = ["选择厂家"]
    rows.extend(f"{index}. {vendor}" for index, vendor in enumerate(vendors, 1))
    rows.append("0. 返回")
    choice = ask("\n".join(rows), [str(index) for index in range(len(vendors) + 1)], "0")
    if choice == "0":
        return None
    return vendors[int(choice) - 1]


def _choose_model(manager: ModelManager, ask: AskFunc) -> str | None:
    vendor = _choose_vendor(manager, ask)
    if vendor is None:
        return None
    profiles = manager.models_for_vendor(vendor)
    rows = [f"选择 {vendor} 模型"]
    rows.extend(f"{index}. {profile.model_id}" for index, profile in enumerate(profiles, 1))
    rows.append("0. 返回")
    choice = ask("\n".join(rows), [str(index) for index in range(len(profiles) + 1)], "0")
    if choice == "0":
        return None
    return profiles[int(choice) - 1].ref


def _set_reasoning_effort(manager: ModelManager, ask: AskFunc) -> MenuResult:
    try:
        profile = manager.active_profile()
    except ValueError:
        return MenuResult("当前未选择模型。请先使用 /model 选择模型。")
    if not profile.supports_thinking:
        return MenuResult(f"模型 {profile.model_id} 不支持思考模式。")
    if not profile.supported_reasoning_efforts:
        return MenuResult(f"模型 {profile.model_id} 不支持设置 reasoning_effort。")
    rows = ["选择 reasoning_effort"]
    rows.extend(
        f"{index}. {effort}"
        for index, effort in enumerate(profile.supported_reasoning_efforts, 1)
    )
    rows.append("0. 返回")
    choice = ask(
        "\n".join(rows),
        [str(index) for index in range(len(profile.supported_reasoning_efforts) + 1)],
        "0",
    )
    if choice == "0":
        return MenuResult("已返回。")
    effort = profile.supported_reasoning_efforts[int(choice) - 1]
    return _operation_to_menu_result(manager.set_reasoning_effort(effort))


def _operation_to_menu_result(result: ModelOperationResult) -> MenuResult:
    return MenuResult(
        message=result.message,
        requires_agent_rebuild=result.requires_agent_rebuild,
    )
