"""Provider-agnostic LLM access: Anthropic first-party API or Amazon Bedrock.

Both providers support strict/forced tool use, so classification goes through a
single forced-tool-call path here rather than provider-specific structured-output
knobs. Callers get back a plain dict of the tool input (or None on any failure).
"""

from __future__ import annotations

from typing import Optional


def build_client(cfg):
    """Construct an Anthropic or AnthropicBedrock client. Raises if the SDK/creds are absent."""
    if cfg.provider == "bedrock":
        from anthropic import AnthropicBedrock

        kwargs = {}
        if cfg.aws_region:
            kwargs["aws_region"] = cfg.aws_region
        # Credentials come from the standard AWS chain (env, ~/.aws, IAM role).
        return AnthropicBedrock(**kwargs)

    from anthropic import Anthropic

    # Key from ANTHROPIC_API_KEY or an `ant auth login` profile.
    return Anthropic()


def resolve_model(cfg) -> str:
    """The model id to send. Bedrock uses its own ids/inference profiles."""
    if cfg.provider == "bedrock":
        return cfg.bedrock_model or cfg.model
    return cfg.model


def force_tool(client, model: str, system: str, prompt: str,
               tool_name: str, tool_description: str, schema: dict,
               max_tokens: int = 1024, errbox: Optional[list] = None) -> Optional[dict]:
    """Force one call to a named tool and return its validated input as a dict.

    Tries the richest request first (strict tool use + thinking disabled) and
    progressively drops features that a given provider/model may reject, so the
    same code works on the first-party API and on Bedrock's assorted model ids.
    Returns None on total failure; if `errbox` is provided, the last error from
    each attempt is appended for diagnostics.
    """
    base = dict(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    # (use_strict, use_thinking) from most to least featureful.
    for use_strict, use_thinking in ((True, True), (True, False), (False, True), (False, False)):
        tool = {"name": tool_name, "description": tool_description, "input_schema": schema}
        if use_strict:
            tool["strict"] = True
        kwargs = dict(base, tools=[tool])
        if use_thinking:
            # Forced tool_choice is incompatible with extended thinking; disable it.
            kwargs["thinking"] = {"type": "disabled"}
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - try a simpler request shape next
            if errbox is not None:
                errbox.append(f"{type(exc).__name__}: {exc}")
            continue
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return dict(block.input) if block.input is not None else None
        if errbox is not None:
            errbox.append("model returned no tool_use block")
        return None
    return None


def diagnose(cfg) -> dict:
    """Attempt a real minimal classification call; return a human-readable report.

    Used by `arxivist doctor` to explain exactly why topic classification is (not)
    working on a given machine.
    """
    report = {"provider": cfg.provider, "use_llm": cfg.use_llm,
              "model": resolve_model(cfg), "ok": False, "detail": ""}
    if not cfg.use_llm:
        report["detail"] = "LLM disabled (use_llm: false / --no-llm)."
        return report
    try:
        client = build_client(cfg)
    except ImportError:
        report["detail"] = ('Anthropic SDK not installed. Run: pip install -e ".[llm]" '
                            '(or ".[all]").')
        return report
    except Exception as exc:  # noqa: BLE001
        report["detail"] = f"Could not construct client: {type(exc).__name__}: {exc}"
        return report

    errbox: list = []
    schema = {
        "type": "object",
        "properties": {"topic": {"type": "string"}},
        "required": ["topic"], "additionalProperties": False,
    }
    got = force_tool(
        client, resolve_model(cfg),
        system="You classify a paper into a short topic.",
        prompt="Paper: 'A study of survival analysis'. Return a topic.",
        tool_name="select_topic", tool_description="Record the topic.",
        schema=schema, max_tokens=256, errbox=errbox,
    )
    if got and got.get("topic"):
        report["ok"] = True
        report["detail"] = f"OK — model returned topic {got['topic']!r}."
    else:
        report["detail"] = "Call failed: " + (errbox[-1] if errbox else "unknown error")
        report["errors"] = errbox
    return report
