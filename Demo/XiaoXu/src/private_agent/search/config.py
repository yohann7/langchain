"""Strict, per-call configuration for the two search tools."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


class SearchConfigError(ValueError):
    """Raised when a search configuration cannot be read or validated."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SearXNGConfig(_StrictFrozenModel):
    max_attempts: StrictPositiveInt
    retry_delays_seconds: tuple[PositiveFloat, ...]

    @field_validator("retry_delays_seconds", mode="before")
    @classmethod
    def freeze_retry_delays(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_retry_count(self) -> "SearXNGConfig":
        if len(self.retry_delays_seconds) != self.max_attempts - 1:
            raise ValueError(
                "retry_delays_seconds length must equal max_attempts - 1"
            )
        return self


class WebSearchConfig(_StrictFrozenModel):
    version: Literal[1]
    max_queries_per_turn: StrictPositiveInt
    max_results_per_query: StrictPositiveInt
    request_timeout_seconds: PositiveFloat
    searxng: SearXNGConfig
    tavily_fallback_enabled: bool


class KnowledgeSearchConfig(_StrictFrozenModel):
    version: Literal[1]
    max_queries_per_turn: StrictPositiveInt
    default_results_per_query: StrictPositiveInt
    max_results_per_query: StrictPositiveInt
    request_timeout_seconds: PositiveFloat

    @model_validator(mode="after")
    def validate_result_limits(self) -> "KnowledgeSearchConfig":
        if self.default_results_per_query > self.max_results_per_query:
            raise ValueError(
                "default_results_per_query must not exceed max_results_per_query"
            )
        if self.max_results_per_query > 20:
            raise ValueError("max_results_per_query must not exceed 20")
        return self


def _load_document(path: Path, *, kind: str) -> dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SearchConfigError(f"{kind} search configuration is invalid") from exc
    if not isinstance(raw, dict):
        raise SearchConfigError(f"{kind} search configuration is invalid")
    return raw


def load_web_search_config(path: str | Path) -> WebSearchConfig:
    """Read and validate one Web search configuration snapshot."""

    try:
        return WebSearchConfig.model_validate(_load_document(Path(path), kind="web"))
    except ValidationError as exc:
        raise SearchConfigError("web search configuration is invalid") from exc


def load_knowledge_search_config(path: str | Path) -> KnowledgeSearchConfig:
    """Read and validate one Knowledge search configuration snapshot."""

    try:
        return KnowledgeSearchConfig.model_validate(
            _load_document(Path(path), kind="knowledge")
        )
    except ValidationError as exc:
        raise SearchConfigError("knowledge search configuration is invalid") from exc
