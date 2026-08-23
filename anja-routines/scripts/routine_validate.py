#!/usr/bin/env python3
"""
routine_validate.py — validazione schema yaml di una routine anja.

Mini-parser yaml stdlib (senza pyyaml dependency) per i nostri schema semplici.
Per yaml più complessi servirebbe pyyaml — per il MVP teniamo stdlib.

Usage:
    python3 routine_validate.py <routine.yaml>
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Optional


REQUIRED_FIELDS = ("name", "scope", "schedule", "prompt")
VALID_MODELS = ("haiku", "sonnet", "opus", "fast")
VALID_PROVIDERS = ("claude", "anthropic", "openai", "openai_oauth", "grok_cli", "openrouter", "xai", "")
VALID_OUTPUT_TYPES = (
    "email", "google_chat", "slack", "telegram",
    "wiki_ingest", "wiki_page_hub", "file", "webhook",
)
CRON_FIELDS_RE = re.compile(
    r"^\s*([\d*/,\-]+)\s+([\d*/,\-]+)\s+([\d*/,\-]+)\s+([\d*/,\-]+)\s+([\d*/,\-]+)\s*$"
)


def parse_simple_yaml(text: str) -> dict:
    """Mini-parser yaml: top-level scalars + lists + nested mappings.

    Supporta:
      key: value
      key: "value"
      key: |
        multi-line
        block
      key:
        - item
      output:
        - type: email
          to: foo
    NON supporta: anchor, references, complex flow style oltre [item, item].
    """
    out: dict = {}
    lines = text.split("\n")
    i = 0
    n = len(lines)
    stack = [(0, out)]  # (indent, container)

    def current_container():
        return stack[-1][1]

    def adjust_stack(indent: int):
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

    while i < n:
        raw = lines[i]
        # skip blank / comment
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue

        # compute indent
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        # list item under a list parent
        if line.startswith("- "):
            adjust_stack(indent + 1)  # list items belong to parent
            parent = current_container()
            if not isinstance(parent, list):
                # Convert: parent must be a list. Look up.
                # Fallback: skip
                i += 1
                continue
            value_part = line[2:].strip()
            # If "- key: value" → start new dict in list
            if ":" in value_part:
                k, _, v = value_part.partition(":")
                k = k.strip()
                v = v.strip()
                obj: dict = {}
                if v:
                    obj[k] = _scalar(v)
                else:
                    obj[k] = None  # may be filled by nested
                parent.append(obj)
                stack.append((indent, obj))
            else:
                parent.append(_scalar(value_part))
            i += 1
            continue

        # key: value | key: | key:
        if ":" in line:
            adjust_stack(indent)
            parent = current_container()
            if not isinstance(parent, dict):
                i += 1
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()

            # block scalar |
            if val == "|":
                i += 1
                buf = []
                base = None
                while i < n:
                    nl = lines[i]
                    if not nl.strip() and base is None:
                        # blank inside block before any text — append empty
                        buf.append("")
                        i += 1
                        continue
                    if not nl.strip():
                        buf.append("")
                        i += 1
                        continue
                    nl_ind = len(nl) - len(nl.lstrip(" "))
                    if base is None:
                        if nl_ind <= indent:
                            break
                        base = nl_ind
                    if nl_ind < base:
                        break
                    buf.append(nl[base:])
                    i += 1
                parent[key] = "\n".join(buf).rstrip("\n")
                continue

            # value present
            if val:
                # inline list "[a, b]"
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    parent[key] = [_scalar(x.strip()) for x in inner.split(",")] if inner else []
                else:
                    parent[key] = _scalar(val)
                i += 1
                continue

            # nested (val == "")
            # peek next line to decide if list or dict.
            # NB: lo stack frame deve usare l'indent della KEY parent, non
            # l'indent dei children — così adjust_stack non rimuove il
            # container quando processiamo i suoi children.
            j = i + 1
            while j < n and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
                j += 1
            if j < n:
                nxt = lines[j]
                nxt_stripped = nxt.lstrip()
                if nxt_stripped.startswith("- "):
                    parent[key] = []
                    stack.append((indent, parent[key]))
                else:
                    parent[key] = {}
                    stack.append((indent, parent[key]))
            else:
                parent[key] = None
            i += 1
            continue

        i += 1

    return out


def _strip_inline_comment(s: str) -> str:
    """Rimuove inline comment da un valore yaml non quotato."""
    s = s.strip()
    if not s:
        return s
    # se interamente quoted, non toccare
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s
    # taglia su " # " (con spazio prima del #)
    idx = s.find(" #")
    if idx > 0:
        return s[:idx].rstrip()
    return s


def _scalar(s: str) -> Any:
    s = _strip_inline_comment(s)
    if not s:
        return ""
    # double-quoted: processa escape sequences
    if s.startswith('"') and s.endswith('"'):
        inner = s[1:-1]
        return (
            inner
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\"", "\"")
            .replace("\\\\", "\\")
        )
    # single-quoted: literal
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    # bool
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none", "~"):
        return None
    # int
    try:
        return int(s)
    except ValueError:
        pass
    # float
    try:
        return float(s)
    except ValueError:
        pass
    return s


# =================================================================
# VALIDATION
# =================================================================

def validate_routine(yaml_obj: dict) -> tuple:
    """Ritorna (errors: list[str], warnings: list[str])."""
    errors = []
    warnings = []

    # Required fields. F-EventTriggers: una routine `trigger: webhook` può non avere
    # schedule (parte solo a evento via POST /hooks/<name>); il segreto è OBBLIGATORIO
    # (fail-closed: endpoint pubblico, senza secret la routine non è invocabile).
    is_webhook = yaml_obj.get("trigger") == "webhook"
    for f in REQUIRED_FIELDS:
        if f == "schedule" and is_webhook:
            continue
        if f not in yaml_obj or yaml_obj[f] in (None, ""):
            errors.append(f"missing required field: '{f}'")
    trigger = yaml_obj.get("trigger")
    if trigger not in (None, "", "webhook"):
        errors.append(f"trigger '{trigger}' not supported (only 'webhook')")
    if is_webhook and not str(yaml_obj.get("hook_secret") or "").strip():
        errors.append("trigger: webhook requires 'hook_secret' (plain or {{VAR}} from .secrets.env)")
    hook_hmac = yaml_obj.get("hook_hmac")
    if hook_hmac not in (None, "", "github"):
        errors.append(f"hook_hmac '{hook_hmac}' not supported (only 'github' = X-Hub-Signature-256)")

    # Name format
    name = yaml_obj.get("name", "")
    if name and not re.match(r"^[a-z0-9][a-z0-9_-]*$", str(name)):
        errors.append(f"name '{name}' must be kebab-case (lowercase, digits, dash, underscore)")

    # Scope
    scope = yaml_obj.get("scope", "")
    if scope and scope != "hub" and not (isinstance(scope, str) and scope.startswith("project:")):
        errors.append(f"scope '{scope}' must be 'hub' or 'project:<name>'")

    # Schedule (cron)
    schedule = yaml_obj.get("schedule", "")
    if schedule and not CRON_FIELDS_RE.match(str(schedule)):
        errors.append(f"schedule '{schedule}' is not a valid cron expression (5 fields)")

    # Provider (optional, default claude) — Fase 7
    provider = yaml_obj.get("provider")
    if provider and provider not in VALID_PROVIDERS:
        warnings.append(f"provider '{provider}' not in known set {VALID_PROVIDERS} (will be passed through to opencode)")

    # Model (optional, default sonnet)
    model = yaml_obj.get("model")
    if model and model not in VALID_MODELS and (provider in (None, "", "claude", "anthropic")):
        warnings.append(f"model '{model}' not in known set {VALID_MODELS} (will be passed through)")

    # Output actions
    outputs = yaml_obj.get("output", [])
    if not isinstance(outputs, list):
        errors.append("output must be a list")
    else:
        for i, out in enumerate(outputs):
            if not isinstance(out, dict):
                errors.append(f"output[{i}] must be a mapping")
                continue
            t = out.get("type")
            if not t:
                errors.append(f"output[{i}] missing 'type'")
            elif t not in VALID_OUTPUT_TYPES:
                warnings.append(f"output[{i}].type '{t}' not in known set {VALID_OUTPUT_TYPES}")

    # Tools
    tools = yaml_obj.get("tools", [])
    if tools and not isinstance(tools, list):
        errors.append("tools must be a list of strings")

    # Timeout
    timeout = yaml_obj.get("timeout_sec")
    if timeout is not None:
        if not isinstance(timeout, int) or timeout < 5 or timeout > 3600:
            warnings.append(f"timeout_sec '{timeout}' should be between 5 and 3600")

    # Routine memory (M-Mem 4)
    related = yaml_obj.get("related_routines")
    if related is not None and not isinstance(related, list):
        errors.append("related_routines must be a list of routine names")
    rmem_n = yaml_obj.get("routine_memory_n")
    if rmem_n is not None:
        if not isinstance(rmem_n, int) or rmem_n < 0 or rmem_n > 20:
            warnings.append(f"routine_memory_n '{rmem_n}' should be between 0 and 20")

    return errors, warnings


def load_and_validate(yaml_path: Path) -> Optional[dict]:
    if not yaml_path.is_file():
        print(f"ERROR: file not found: {yaml_path}", file=sys.stderr)
        return None
    text = yaml_path.read_text(encoding="utf-8")
    try:
        obj = parse_simple_yaml(text)
    except Exception as e:
        print(f"ERROR: parse failure in {yaml_path.name}: {e}", file=sys.stderr)
        return None
    errors, warnings = validate_routine(obj)
    if errors:
        print(f"❌ {yaml_path.name} — {len(errors)} errors:", file=sys.stderr)
        for e in errors:
            print(f"   - {e}", file=sys.stderr)
        return None
    if warnings:
        print(f"⚠️  {yaml_path.name} — {len(warnings)} warnings:")
        for w in warnings:
            print(f"   - {w}")
    return obj


def main():
    p = argparse.ArgumentParser(description="Validate a anja routine yaml.")
    p.add_argument("yaml_file", help="path to routine yaml")
    args = p.parse_args()

    obj = load_and_validate(Path(args.yaml_file))
    if obj is None:
        sys.exit(1)

    print(f"✅ {args.yaml_file} — valid routine")
    print(f"   name:     {obj.get('name')}")
    print(f"   scope:    {obj.get('scope')}")
    print(f"   schedule: {obj.get('schedule')}")
    print(f"   model:    {obj.get('model', 'sonnet (default)')}")
    print(f"   tools:    {obj.get('tools', '(default by scope)')}")
    print(f"   outputs:  {len(obj.get('output', []))} action(s)")


if __name__ == "__main__":
    main()
