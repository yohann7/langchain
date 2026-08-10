"""Sensitive-value redaction before managed storage and indexing."""

from dataclasses import dataclass
import re


MASK = "********"


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redaction_count: int
    sensitivity: str

    @property
    def was_redacted(self) -> bool:
        return self.redaction_count > 0

    def has_meaningful_content(self, minimum_characters: int) -> bool:
        visible = re.sub(r"[^\w\u4e00-\u9fff]+", "", self.text.replace(MASK, ""))
        return len(visible) >= minimum_characters


_ASSIGNMENT = re.compile(
    r"(?im)\b([A-Z0-9_]*(?:API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD))\s*([=:])\s*([^\s'\"]{12,})"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_content(content: str) -> RedactionResult:
    count = 0

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}{MASK}"

    def private_key(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return MASK

    text = _ASSIGNMENT.sub(assignment, content)
    text = _PRIVATE_KEY.sub(private_key, text)
    return RedactionResult(
        text=text,
        redaction_count=count,
        sensitivity="sensitive" if count else "normal",
    )
