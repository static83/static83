"""One function: ask a role for a Memo. The role prompt is the system prompt; the case is the
user message; the Memo schema is enforced by the API."""
from __future__ import annotations

import json
from functools import lru_cache

import anthropic

from .config import ROLES_DIR, settings
from .schema import Memo

PREAMBLE = (
    "You are one of six Dots in an overnight research crew routed by a program called the Spider. "
    "You will receive the original source and every memo written before yours. Reply with a single "
    "Memo object. Carry forward every earlier objection in `objections`, marking each as answered "
    "or open. Do not invent prices or figures that are not in the material you were given; if a "
    "number is missing, say so in `reasoning` and reflect it in your verdict. Keep the headline "
    "under 90 characters.\n\n"
)

_schema = Memo.model_json_schema()
_schema["additionalProperties"] = False


@lru_cache(maxsize=None)
def role_prompt(role: str) -> str:
    return (ROLES_DIR / f"{role.lower()}.md").read_text()


@lru_cache(maxsize=1)
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY or an `ant auth login` profile


def ask_role(role: str, case_packet: dict) -> Memo:
    """Call Claude as `role` with the full case packet and parse the Memo it returns."""
    system = [
        {"type": "text", "text": PREAMBLE + role_prompt(role), "cache_control": {"type": "ephemeral"}}
    ]
    user = (
        f"You are {role}. Here is the case packet as JSON. Write your memo.\n\n"
        + json.dumps(case_packet, indent=2, default=str)
    )
    response = client().beta.messages.create(
        model=settings.model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": _schema},
        },
        # Server-side refusal fallback: if a safety classifier declines, the same request is
        # re-run on a fallback model inside this call. Drop these two lines to opt out.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{role}: the model declined this case ({response.stop_details})")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{role}: memo was cut off at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    memo = Memo.model_validate_json(text)
    memo.role = role  # the schema allows any role; the Spider decides who is speaking
    return memo
