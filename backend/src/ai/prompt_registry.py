"""Versioned prompt templates as code (ADR 0011). Each template is a directory under
``prompts/`` with a ``template.md`` (role-sectioned body with ``{variable}`` slots) and
a ``meta.yaml`` (name, semver version, declared variables, owner feature, body hash,
changelog). The registry loads them at startup and FAILS FAST on a duplicate name, an
undeclared variable used in the body, a declared-but-unused variable, or a body whose
hash doesn't match ``meta.yaml`` (an edit without a version/hash bump). Rendering is
strict: the provided variables must match the declared set exactly — never silently
interpolate empty. The rendered ``template_version`` flows into the trace + usage event.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Formatter

import yaml

from src.ai.gateway import ChatMessage, RenderedPrompt

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_ROLE_HEADER = re.compile(r"^#\s+(system|user|assistant)\s*$", re.IGNORECASE)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class PromptError(Exception):
    """A prompt template is malformed, or a render call violated the strict contract."""


@dataclass(frozen=True, slots=True)
class _Template:
    name: str
    version: str
    owner_feature: str
    variables: frozenset[str]
    messages: tuple[ChatMessage, ...]


def _parse_messages(body: str) -> tuple[ChatMessage, ...]:
    """Split a role-sectioned ``template.md`` into messages. Lines like ``# system`` /
    ``# user`` / ``# assistant`` start a section; content runs until the next header."""
    messages: list[ChatMessage] = []
    role: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        header = _ROLE_HEADER.match(line)
        if header:
            if role is not None:
                messages.append(ChatMessage(role, "\n".join(lines).strip()))
            role = header.group(1).lower()
            lines = []
        else:
            lines.append(line)
    if role is not None:
        messages.append(ChatMessage(role, "\n".join(lines).strip()))
    return tuple(messages)


def _slots(messages: tuple[ChatMessage, ...]) -> set[str]:
    """Variable names referenced as ``{name}`` across all message bodies (``{{`` is a
    literal brace). Rejects empty / attribute / index field expressions."""
    used: set[str] = set()
    for message in messages:
        for _literal, field, _spec, _conv in Formatter().parse(message.content):
            if field is None:
                continue
            if not field or not _VALID_NAME.match(field):
                raise PromptError(f"invalid variable slot {{{field}}} — use simple names")
            used.add(field)
    return used


def _load_template(directory: Path) -> _Template:
    meta = yaml.safe_load((directory / "meta.yaml").read_text())
    raw = (directory / "template.md").read_bytes()
    name = meta["name"]
    if not _VALID_NAME.match(name):
        raise PromptError(f"template '{name}': name must be snake_case")
    if not _SEMVER.match(str(meta["version"])):
        raise PromptError(f"template '{name}': version must be semver (X.Y.Z)")
    expected_hash = hashlib.sha256(raw).hexdigest()
    if meta.get("body_sha256") != expected_hash:
        raise PromptError(
            f"template '{name}': body_sha256 mismatch — template.md changed without "
            f"updating meta.yaml (bump the version). expected {expected_hash}"
        )
    messages = _parse_messages(raw.decode())
    declared = {str(v) for v in (meta.get("variables") or {})}
    used = _slots(messages)
    if used - declared:
        raise PromptError(
            f"template '{name}': undeclared variables in body: {sorted(used - declared)}"
        )
    if declared - used:
        raise PromptError(
            f"template '{name}': declared-but-unused variables: {sorted(declared - used)}"
        )
    return _Template(
        name=name,
        version=str(meta["version"]),
        owner_feature=str(meta["owner_feature"]),
        variables=frozenset(declared),
        messages=messages,
    )


class PromptRegistry:
    def __init__(self, templates: dict[str, _Template]) -> None:
        self._templates = templates

    def render(self, name: str, /, **variables: str) -> RenderedPrompt:
        """Render ``name`` with ``variables``. The provided keys must equal the declared
        set exactly — a missing OR extra variable raises (never a silent empty slot).

        Time: O(total template length). Space: O(rendered length).
        """
        template = self._templates.get(name)
        if template is None:
            raise PromptError(f"unknown prompt template '{name}'")
        provided = set(variables)
        if provided != template.variables:
            missing = sorted(template.variables - provided)
            extra = sorted(provided - template.variables)
            raise PromptError(
                f"template '{name}': variable mismatch (missing={missing}, extra={extra})"
            )
        messages = tuple(
            ChatMessage(m.role, m.content.format(**variables)) for m in template.messages
        )
        variables_hash = hashlib.sha256(json.dumps(variables, sort_keys=True).encode()).hexdigest()
        return RenderedPrompt(
            template_name=name,
            template_version=template.version,
            messages=messages,
            variables_hash=variables_hash,
        )


def load_registry(prompts_dir: Path = _PROMPTS_DIR) -> PromptRegistry:
    """Load + validate every template, failing fast on the first problem (incl. a
    duplicate name). Time: O(templates · length)."""
    templates: dict[str, _Template] = {}
    for directory in sorted(p for p in prompts_dir.iterdir() if (p / "meta.yaml").exists()):
        template = _load_template(directory)
        if template.name in templates:
            raise PromptError(f"duplicate template name '{template.name}'")
        templates[template.name] = template
    return PromptRegistry(templates)


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    """Process-wide registry, loaded + validated once at first use (fail-fast)."""
    return load_registry()
