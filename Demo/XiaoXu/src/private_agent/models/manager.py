"""Model catalog, model state, and provider-specific runtime options."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

from dotenv import dotenv_values
from langchain_openai import ChatOpenAI

from private_agent.config import AppSettings
from private_agent.models.catalog import load_model_catalog
from private_agent.persistence.database import XiaoXuDatabase
from private_agent.persistence.model_state import ModelStateStore


@dataclass(frozen=True)
class ModelProfile:
    """Non-sensitive metadata for one chat model."""

    vendor: str
    model_id: str
    display_name: str
    model_provider: str
    api_key_env: str
    base_url_env: str
    supports_thinking: bool = False
    default_thinking_enabled: bool | None = None
    supported_reasoning_efforts: tuple[str, ...] = ()
    context_window: int | None = None
    deprecated: bool = False
    notes: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.vendor}.{self.model_id}"


@dataclass
class ThinkingState:
    """Persisted thinking settings for a model."""

    enabled: bool | None = None
    reasoning_effort: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reasoning_effort": self.reasoning_effort,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ThinkingState":
        if not isinstance(raw, dict):
            return cls()
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            enabled = None
        effort = raw.get("reasoning_effort")
        if not isinstance(effort, str):
            effort = None
        return cls(enabled=enabled, reasoning_effort=effort)


@dataclass
class ModelOperationResult:
    """Result for model and thinking operations."""

    message: str
    changed: bool = False
    requires_agent_rebuild: bool = False


@dataclass
class ModelRuntimeState:
    """Persisted local model selection state."""

    active_model: str | None = None
    thinking: dict[str, ThinkingState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_model": self.active_model,
            "thinking": {
                model_ref: state.to_dict()
                for model_ref, state in sorted(self.thinking.items())
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, default_active: str | None) -> "ModelRuntimeState":
        if not isinstance(raw, dict):
            return cls(active_model=default_active)
        active = raw.get("active_model")
        if not isinstance(active, str):
            active = default_active
        raw_thinking = raw.get("thinking")
        thinking: dict[str, ThinkingState] = {}
        if isinstance(raw_thinking, dict):
            for model_ref, state in raw_thinking.items():
                if isinstance(model_ref, str):
                    thinking[model_ref] = ThinkingState.from_dict(state)
        return cls(active_model=active, thinking=thinking)


class ReasoningPreservingChatOpenAI(ChatOpenAI):
    """OpenAI-compatible chat model that keeps provider reasoning deltas."""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None
        delta = _first_choice_delta(chunk)
        if delta:
            _copy_reasoning_delta(generation_chunk.message.additional_kwargs, delta)
        return generation_chunk


def init_openai_compatible_chat_model(**kwargs: Any) -> ReasoningPreservingChatOpenAI:
    """Build the chat model used for OpenAI-compatible providers."""

    return ReasoningPreservingChatOpenAI(**kwargs)


def _first_choice_delta(chunk: dict) -> dict[str, Any]:
    choices = (
        chunk.get("choices", [])
        or chunk.get("chunk", {}).get("choices", [])
    )
    if not choices:
        return {}
    delta = choices[0].get("delta")
    return delta if isinstance(delta, dict) else {}


def _copy_reasoning_delta(
    additional_kwargs: dict[str, Any],
    delta: dict[str, Any],
) -> None:
    for key in (
        "reasoning_content",
        "reasoning",
        "thinking",
        "reasoning_details",
        "reasoning_delta",
    ):
        value = delta.get(key)
        if value:
            additional_kwargs[key] = value


def _profile(
    vendor: str,
    model_id: str,
    display_name: str,
    api_key_env: str,
    base_url_env: str,
    *,
    supports_thinking: bool = False,
    default_thinking_enabled: bool | None = None,
    supported_reasoning_efforts: tuple[str, ...] = (),
    context_window: int | None = None,
    deprecated: bool = False,
    notes: tuple[str, ...] = (),
) -> ModelProfile:
    return ModelProfile(
        vendor=vendor,
        model_id=model_id,
        display_name=display_name,
        model_provider="openai",
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        supports_thinking=supports_thinking,
        default_thinking_enabled=default_thinking_enabled,
        supported_reasoning_efforts=supported_reasoning_efforts,
        context_window=context_window,
        deprecated=deprecated,
        notes=notes,
    )


class ModelManager:
    """Manage model metadata, selected model, and thinking settings."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        database: XiaoXuDatabase | None = None,
    ):
        self.settings = settings
        raw_catalog = settings.model_catalog or load_model_catalog(
            settings.model_catalog_path.expanduser()
        )
        self._catalog = self._build_catalog(raw_catalog)
        self._database = database or XiaoXuDatabase(
            settings.resolve_in_run_dir(settings.sqlite_database_path)
        )
        self._state_store = ModelStateStore(self._database)
        self._state = self._load_state()

    def vendors(self) -> list[str]:
        return sorted(self._catalog)

    def models_for_vendor(self, vendor: str) -> list[ModelProfile]:
        return list(self._catalog.get(vendor.lower(), ()))

    def all_profiles(self) -> list[ModelProfile]:
        profiles: list[ModelProfile] = []
        for vendor in self.vendors():
            profiles.extend(self.models_for_vendor(vendor))
        return profiles

    def get_profile(self, model_ref: str) -> ModelProfile:
        vendor, model_id = self._parse_ref(model_ref)
        for profile in self.models_for_vendor(vendor):
            if profile.model_id == model_id:
                return profile
        raise ValueError(f"Unknown model: {model_ref}")

    def has_active_model(self) -> bool:
        return self._state.active_model is not None

    def active_model_ref(self) -> str | None:
        return self._state.active_model

    def active_profile(self) -> ModelProfile:
        if not self._state.active_model:
            raise ValueError("No active model selected. Use /model to select one.")
        return self.get_profile(self._state.active_model)

    def current_thinking(self) -> ThinkingState:
        active = self.active_model_ref()
        if active is None:
            return ThinkingState()
        return self._state.thinking.get(active, ThinkingState())

    def effective_thinking(self) -> ThinkingState:
        profile = self.active_profile()
        current = self.current_thinking()
        enabled = current.enabled
        if enabled is None:
            enabled = profile.default_thinking_enabled
        return ThinkingState(enabled=enabled, reasoning_effort=current.reasoning_effort)

    def switch_model(self, model_ref: str) -> ModelOperationResult:
        profile = self.get_profile(model_ref)
        if self._state.active_model == profile.ref:
            return ModelOperationResult(f"当前模型已经是 {profile.ref}。")
        self._state.active_model = profile.ref
        self._save_state()
        return ModelOperationResult(
            f"已切换模型为 {profile.ref}。",
            changed=True,
            requires_agent_rebuild=True,
        )

    def set_thinking_enabled(self, enabled: bool) -> ModelOperationResult:
        profile = self._active_profile_or_none()
        if profile is None:
            return ModelOperationResult("当前未选择模型。请先使用 /model 选择模型。")
        if not profile.supports_thinking:
            return ModelOperationResult(f"模型 {profile.model_id} 不支持思考模式。")
        state = self._state.thinking.setdefault(profile.ref, ThinkingState())
        state.enabled = enabled
        self._save_state()
        action = "开启" if enabled else "关闭"
        return ModelOperationResult(
            f"已{action}思考模式：{profile.model_id}。",
            changed=True,
            requires_agent_rebuild=True,
        )

    def set_reasoning_effort(self, effort: str) -> ModelOperationResult:
        profile = self._active_profile_or_none()
        if profile is None:
            return ModelOperationResult("当前未选择模型。请先使用 /model 选择模型。")
        if not profile.supports_thinking:
            return ModelOperationResult(f"模型 {profile.model_id} 不支持思考模式。")
        if not profile.supported_reasoning_efforts:
            return ModelOperationResult(f"模型 {profile.model_id} 不支持设置 reasoning_effort。")
        if effort not in profile.supported_reasoning_efforts:
            allowed = ", ".join(profile.supported_reasoning_efforts)
            return ModelOperationResult(f"reasoning_effort 只能是：{allowed}。")
        state = self._state.thinking.setdefault(profile.ref, ThinkingState())
        state.reasoning_effort = effort
        self._save_state()
        return ModelOperationResult(
            f"reasoning_effort 已设置为 {effort}。",
            changed=True,
            requires_agent_rebuild=True,
        )

    def reset_thinking(self) -> ModelOperationResult:
        profile = self._active_profile_or_none()
        if profile is None:
            return ModelOperationResult("当前未选择模型。请先使用 /model 选择模型。")
        if not profile.supports_thinking:
            return ModelOperationResult(f"模型 {profile.model_id} 不支持思考模式。")
        self._state.thinking.pop(profile.ref, None)
        self._save_state()
        return ModelOperationResult(
            f"已恢复 {profile.model_id} 的默认思考配置。",
            changed=True,
            requires_agent_rebuild=True,
        )

    def build_chat_model(self):
        profile = self.active_profile()
        api_key = self._secret_value(profile.api_key_env)
        base_url = self._secret_value(profile.base_url_env)
        missing = [
            env_name
            for env_name, value in (
                (profile.api_key_env, api_key),
                (profile.base_url_env, base_url),
            )
            if not value
        ]
        if missing:
            raise ValueError("Missing model environment variables: " + ", ".join(missing))

        kwargs: dict[str, Any] = {
            "model": profile.model_id,
            "api_key": api_key,
            "base_url": base_url,
        }
        thinking = self.effective_thinking()
        if profile.supports_thinking and thinking.enabled is not None:
            kwargs["extra_body"] = {
                "thinking": {
                    "type": "enabled" if thinking.enabled else "disabled",
                }
            }
        if profile.supports_thinking and thinking.reasoning_effort:
            kwargs["reasoning_effort"] = thinking.reasoning_effort
        return init_openai_compatible_chat_model(**kwargs)

    def format_current(self) -> str:
        profile = self._active_profile_or_none()
        if profile is None:
            return "当前未选择模型。"
        thinking = self.effective_thinking()
        return "\n".join(
            [
                f"当前模型：{profile.ref}",
                f"厂家：{profile.vendor}",
                f"模型名称：{profile.model_id}",
                f"支持思考模式：{'是' if profile.supports_thinking else '否'}",
                f"思考模式：{self._format_thinking_enabled(thinking.enabled)}",
                f"reasoning_effort：{thinking.reasoning_effort or '未设置'}",
            ]
        )

    def format_vendors(self) -> str:
        return "\n".join(f"{index}. {vendor}" for index, vendor in enumerate(self.vendors(), 1))

    def format_all_models(self) -> str:
        rows = []
        for profile in self.all_profiles():
            rows.append(self._format_profile_row(profile))
        return "\n".join(rows)

    def format_models_for_vendor(self, vendor: str) -> str:
        profiles = self.models_for_vendor(vendor)
        if not profiles:
            return f"未知厂家：{vendor}"
        return "\n".join(self._format_profile_row(profile) for profile in profiles)

    def format_profile(self, model_ref: str) -> str:
        profile = self.get_profile(model_ref)
        rows = [
            f"模型：{profile.ref}",
            f"显示名：{profile.display_name}",
            f"Provider：{profile.model_provider}",
            f"支持思考模式：{'是' if profile.supports_thinking else '否'}",
            f"默认思考模式：{self._format_thinking_enabled(profile.default_thinking_enabled)}",
            "reasoning_effort："
            + (", ".join(profile.supported_reasoning_efforts) if profile.supported_reasoning_efforts else "不支持"),
            f"弃用：{'是' if profile.deprecated else '否'}",
        ]
        if profile.notes:
            rows.append("备注：" + "；".join(profile.notes))
        return "\n".join(rows)

    def format_config_status(self) -> str:
        rows = []
        for vendor in self.vendors():
            profiles = self.models_for_vendor(vendor)
            if not profiles:
                continue
            first = profiles[0]
            configured = bool(
                self._secret_value(first.api_key_env)
                and self._secret_value(first.base_url_env)
            )
            rows.append(f"{vendor}: 连接配置={'已完整设置' if configured else '未完整设置'}")
        return "\n".join(rows)

    def _active_profile_or_none(self) -> ModelProfile | None:
        if self._state.active_model is None:
            return None
        return self.get_profile(self._state.active_model)

    def _load_state(self) -> ModelRuntimeState:
        raw = self._state_store.load(user_id=self.settings.user_id)
        if raw is None:
            return ModelRuntimeState(active_model=self.settings.active_model)
        return ModelRuntimeState.from_dict(raw, self.settings.active_model)

    def _save_state(self) -> None:
        self._state_store.save(
            user_id=self.settings.user_id,
            payload=self._state.to_dict(),
        )

    def _secret_value(self, env_name: str) -> str | None:
        value = os.getenv(env_name)
        if value:
            return value
        dotenv_value = dotenv_values(".env").get(env_name)
        if dotenv_value:
            return dotenv_value
        return None

    def _build_catalog(self, yaml_catalog: dict[str, Any]) -> dict[str, tuple[ModelProfile, ...]]:
        catalog: dict[str, list[ModelProfile]] = {}
        for vendor, vendor_config in yaml_catalog.items():
            if not isinstance(vendor_config, dict):
                continue
            api_key_env = str(vendor_config.get("api_key_env") or "").strip()
            base_url_env = str(vendor_config.get("base_url_env") or "").strip()
            raw_models = vendor_config.get("models", [])
            if not api_key_env or not base_url_env or not isinstance(raw_models, list):
                continue
            profiles = []
            for raw_model in raw_models:
                if not isinstance(raw_model, dict) or not raw_model.get("model_id"):
                    continue
                efforts = raw_model.get("supported_reasoning_efforts") or ()
                profiles.append(
                    _profile(
                        str(vendor).lower(),
                        str(raw_model["model_id"]),
                        str(raw_model.get("display_name") or raw_model["model_id"]),
                        api_key_env,
                        base_url_env,
                        supports_thinking=bool(raw_model.get("supports_thinking", False)),
                        default_thinking_enabled=raw_model.get("default_thinking_enabled"),
                        supported_reasoning_efforts=tuple(str(item) for item in efforts),
                        context_window=(
                            int(raw_model["context_window"])
                            if raw_model.get("context_window") is not None
                            else None
                        ),
                        deprecated=bool(raw_model.get("deprecated", False)),
                        notes=tuple(str(item) for item in (raw_model.get("notes") or ())),
                    )
                )
            if profiles:
                catalog[str(vendor).lower()] = profiles
        if not catalog:
            raise ValueError("Model catalog does not contain any valid models.")
        return {vendor: tuple(profiles) for vendor, profiles in catalog.items()}

    @staticmethod
    def _parse_ref(model_ref: str) -> tuple[str, str]:
        if "." not in model_ref:
            raise ValueError(f"Model ref must use vendor.model_id format: {model_ref}")
        vendor, model_id = model_ref.split(".", 1)
        return vendor.lower(), model_id

    @staticmethod
    def _format_profile_row(profile: ModelProfile) -> str:
        tags = []
        if profile.supports_thinking:
            tags.append("thinking")
        if profile.deprecated:
            tags.append("deprecated")
        tag_text = f" [{' '.join(tags)}]" if tags else ""
        return f"{profile.ref} - {profile.display_name}{tag_text}"

    @staticmethod
    def _format_thinking_enabled(enabled: bool | None) -> str:
        if enabled is True:
            return "开启"
        if enabled is False:
            return "关闭"
        return "模型默认"
