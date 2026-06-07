"""Prompt registry: the seed templates load + hash-pin (CI guard), strict rendering
rejects missing AND extra variables, the fail-fast branches all raise, and the
template version flows through the gateway into the trace."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from src.ai.gateway import GatewayContext
from src.ai.models import UsageFeature
from src.ai.prompt_registry import (
    PromptError,
    get_prompt_registry,
    load_registry,
)
from src.ai.tracing import TracingGateway
from tests.ai.test_tracing import _RecordingTracer
from tests.fakes.fake_gateway import FakeGateway


def _make_template(
    parent: Path,
    dirname: str,
    *,
    name: str,
    body: str,
    variables: list[str],
    version: str = "1.0.0",
    correct_hash: bool = True,
) -> None:
    directory = parent / dirname
    directory.mkdir()
    (directory / "template.md").write_text(body)
    digest = hashlib.sha256(body.encode()).hexdigest() if correct_hash else "deadbeef"
    vars_yaml = "\n".join(f"  {v}: str" for v in variables) or "  {}"
    (directory / "meta.yaml").write_text(
        f"name: {name}\nversion: {version}\nowner_feature: chat\n"
        f"variables:\n{vars_yaml}\nbody_sha256: {digest}\nchangelog: []\n"
    )


# --- the real seed templates (this load IS the CI hash-pin guard) ---


def test_seed_templates_load_and_hash_pin() -> None:
    registry = load_registry()  # raises if a template.md changed without a meta bump
    rendered = registry.render(
        "grounded_answer", summary="", history="", sources="[1] the sky is blue", question="color?"
    )
    assert rendered.template_version == "1.1.0"
    assert [m.role for m in rendered.messages] == ["system", "user"]
    assert "the sky is blue" in rendered.messages[1].content
    assert rendered.variables_hash  # stable hash of the variables


def test_judge_template_preserves_literal_json_braces() -> None:
    rendered = get_prompt_registry().render(
        "eval_judge_faithfulness", question="q", context="c", answer="a"
    )
    # {{...}} in the body renders to literal {...} (the model is told to emit JSON)
    assert '{"faithfulness"' in rendered.messages[0].content


# --- strict rendering (both directions) ---


def test_render_rejects_missing_variable() -> None:
    with pytest.raises(PromptError, match="missing="):
        get_prompt_registry().render("grounded_answer", summary="", history="", sources="s")


def test_render_rejects_extra_variable() -> None:
    with pytest.raises(PromptError, match="extra="):
        get_prompt_registry().render(
            "grounded_answer", summary="", history="", sources="s", question="q", junk="z"
        )


def test_render_unknown_template_raises() -> None:
    with pytest.raises(PromptError, match="unknown prompt template"):
        get_prompt_registry().render("does_not_exist")


# --- fail-fast loading branches ---


def test_load_rejects_undeclared_variable_in_body(tmp_path: Path) -> None:
    _make_template(tmp_path, "t", name="t", body="# user\n{a} and {b}", variables=["a"])
    with pytest.raises(PromptError, match="undeclared variables"):
        load_registry(tmp_path)


def test_load_rejects_declared_but_unused_variable(tmp_path: Path) -> None:
    _make_template(tmp_path, "t", name="t", body="# user\n{a}", variables=["a", "b"])
    with pytest.raises(PromptError, match="declared-but-unused"):
        load_registry(tmp_path)


def test_load_rejects_hash_mismatch_without_bump(tmp_path: Path) -> None:
    _make_template(tmp_path, "t", name="t", body="# user\n{a}", variables=["a"], correct_hash=False)
    with pytest.raises(PromptError, match="body_sha256 mismatch"):
        load_registry(tmp_path)


def test_load_rejects_duplicate_name(tmp_path: Path) -> None:
    _make_template(tmp_path, "one", name="dup", body="# user\n{a}", variables=["a"])
    _make_template(tmp_path, "two", name="dup", body="# user\n{b}", variables=["b"])
    with pytest.raises(PromptError, match="duplicate template name"):
        load_registry(tmp_path)


# --- version flows into the trace ---


async def test_template_version_flows_into_trace() -> None:
    rendered = get_prompt_registry().render(
        "grounded_answer", summary="", history="", sources="[1] c", question="q"
    )
    inner = FakeGateway()
    tracer = _RecordingTracer()
    ctx = GatewayContext(uuid4(), uuid4(), UsageFeature.CHAT, "req")
    await TracingGateway(inner=inner, tracer=tracer).complete(prompt=rendered, ctx=ctx)
    assert tracer.traces[0].template_name == "grounded_answer"
    assert tracer.traces[0].template_version == "1.1.0"
