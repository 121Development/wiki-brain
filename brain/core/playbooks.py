"""Playbook loading: canonical prompt/workflow files in the vault.

Playbooks live in <vault>/playbooks/{integrate,curate,synthesize}.md and are
the single source of truth for LLM-pass behavior. Each contains:

- a system-prompt section between the markers below, which the internal
  Python passes load at runtime (curation behavior is knowledge-domain
  policy, so it belongs in the vault, editable without touching code);
- workflow instructions for external agents (which tools to call, the JSON
  plan schema, how to apply via `brain apply-plan` / `brain_apply_plan`).

Internal and external executors therefore run from the same prompt text and
converge on the same validated apply path — the only variable is which model
produced the plan. If a playbook or its markers are missing, passes fall
back to built-in defaults so the brain never breaks on a missing file.
"""
from __future__ import annotations

import re

from brain.config import Config

BEGIN = "<!-- BEGIN SYSTEM PROMPT -->"
END = "<!-- END SYSTEM PROMPT -->"


def load_system_prompt(config: Config, name: str, fallback: str) -> str:
    """Return the system prompt from playbooks/<name>.md, or fallback."""
    path = config.vault_path / "playbooks" / f"{name}.md"
    if not path.is_file():
        return fallback
    text = path.read_text(encoding="utf-8")
    m = re.search(re.escape(BEGIN) + r"(.*?)" + re.escape(END), text, re.DOTALL)
    if not m:
        return fallback
    prompt = m.group(1).strip()
    return prompt or fallback
