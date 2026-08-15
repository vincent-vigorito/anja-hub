#!/usr/bin/env python3
"""users_init.py — crea un user profile in <hub>/users/ (Fase 12 M-Id 1).

Usage:
    python3 users_init.py --hub <hub-path> --name <Name> [--slug <slug>] \\
        [--language it] [--timezone Europe/Rome] [--pronouns ""] [--default]

Crea due file:
    <hub>/users/<slug>.md          (HOT, sempre injected)
    <hub>/users/<slug>-detail.md   (DETAIL, on-demand)

Se `--default` setta `default_user: <slug>` nel <hub>/config.json.
Stdlib only.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s or "user"


def render_template(text: str, mapping: dict) -> str:
    out = text
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def patch_hub_config(hub: Path, key: str, value) -> None:
    cfg_path = hub / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    else:
        cfg = {}
    cfg[key] = value
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Crea user profile in <hub>/users/")
    ap.add_argument("--hub", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--language", default="it")
    ap.add_argument("--timezone", default="")
    ap.add_argument("--pronouns", default="")
    ap.add_argument("--default", action="store_true",
                    help="Setta default_user nel hub config.json")
    ap.add_argument("--force", action="store_true",
                    help="Sovrascrivi se file esistono")
    args = ap.parse_args()

    hub = Path(args.hub).expanduser().resolve()
    if not hub.is_dir():
        print(f"ERROR: hub not found: {hub}", file=sys.stderr)
        return 2

    slug = args.slug or slugify(args.name)
    users_dir = hub / "users"
    users_dir.mkdir(exist_ok=True)

    here = Path(__file__).resolve().parent
    skel = here.parent / "templates" / "user-skeleton"
    hot_tpl = (skel / "USER.md").read_text(encoding="utf-8")
    det_tpl = (skel / "USER-detail.md").read_text(encoding="utf-8")

    mapping = {
        "user_name": args.name,
        "user_slug": slug,
        "pronouns": args.pronouns or "",
        "language": args.language,
        "timezone": args.timezone or "",
        "is_default": "true" if args.default else "false",
        "today": date.today().isoformat(),
    }

    hot_path = users_dir / f"{slug}.md"
    det_path = users_dir / f"{slug}-detail.md"

    if hot_path.exists() and not args.force:
        print(f"ERROR: {hot_path} already exists. Use --force to overwrite.", file=sys.stderr)
        return 3

    hot_path.write_text(render_template(hot_tpl, mapping), encoding="utf-8")
    det_path.write_text(render_template(det_tpl, mapping), encoding="utf-8")

    if args.default:
        patch_hub_config(hub, "default_user", slug)

    print(json.dumps({
        "ok": True,
        "slug": slug,
        "hot": str(hot_path),
        "detail": str(det_path),
        "default": args.default,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
