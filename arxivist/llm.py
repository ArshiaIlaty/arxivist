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
               max_tokens: int = 1024) -> Optional[dict]:
    """Force one call to a named tool and return its validated input as a dict.

    Thinking is disabled: forced tool_choice is incompatible with extended
    thinking, and topic classification doesn't need it. Returns None on any
    network/auth/parse error so the caller can fall back gracefully.
    """
    tool = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": schema,
        # Strict tool use — the model must produce input matching the schema.
        "strict": True,
    }
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            thinking={"type": "disabled"},
        )
    except TypeError:
        # Some SDK/provider combinations reject `strict`; retry without it.
        tool.pop("strict", None)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
                thinking={"type": "disabled"},
            )
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001 - auth/quota/network: degrade to fallback
        return None

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input) if block.input is not None else None
    return None
