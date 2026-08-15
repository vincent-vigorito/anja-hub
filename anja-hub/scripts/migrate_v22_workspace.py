#!/usr/bin/env python3
"""migrate_v22_workspace.py — migrazione hub a Fase 22 (Workspace unificato).

Cosa fa (idempotente, safe su re-run):
  1. Crea `<hub>/workspaces/` se non esiste
  2. Sposta symlinks da `<hub>/projects/<name>` → `<hub>/workspaces/<name>` (preserva back-compat)
  3. Lascia `<hub>/projects/` come SYMLINK a `<hub>/workspaces/` (back-compat per script vecchi)
  4. Scaffold `<hub>/files/`, `<hub>/data/`, `<hub>/scripts/` (Anja's workspace files)
  5. Crea/aggiorna `<hub>/meta.yaml` con `kind: hub`
  6. Per ogni workspace symlink, crea `<workspace>/meta.local.yaml` con kind: external
     (NON tocca il .anjawiki linkato, mette il marker accanto al symlink in <hub>/workspaces/<name>.meta.yaml)

Usage:
  python3 migrate_v22_workspace.py --hub ~/Documents/TEST-HUB [--dry-run]

Stdlib only. Output JSON con report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def write_yaml_simple(path: Path, data: dict) -> None:
    """Scrive YAML semplice (no lib esterne). Solo key: value, niente nested complex."""
    lines = []
    for k, v in data.items():
        if isinstance(v, str):
            # quote se contiene caratteri speciali
            if ":" in v or "#" in v or v.startswith(" "):
                v = f'"{v}"'
            lines.append(f"{k}: {v}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_yaml_simple(path: Path) -> dict:
    """Parser YAML minimale (key: value per riga, no nested)."""
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def step_create_workspaces_dir(hub: Path, dry: bool, report: dict) -> None:
    ws_dir = hub / "workspaces"
    if ws_dir.is_symlink():
        report["workspaces_dir"] = "already symlink (back-compat?)"
        return
    if ws_dir.is_dir():
        report["workspaces_dir"] = "already exists"
        return
    if not dry:
        ws_dir.mkdir(parents=True, exist_ok=True)
    report["workspaces_dir"] = "created"


def step_move_projects_to_workspaces(hub: Path, dry: bool, report: dict) -> None:
    projects_dir = hub / "projects"
    workspaces_dir = hub / "workspaces"
    moved = []
    skipped = []

    if projects_dir.is_symlink():
        # già una symlink (probabile back-compat post-migrazione)
        target = projects_dir.resolve()
        if target == workspaces_dir.resolve():
            report["projects_symlink"] = "already points to workspaces (idempotent)"
            return
        else:
            report["projects_symlink"] = f"unexpected symlink target: {target}"
            return

    if not projects_dir.is_dir():
        report["projects_dir"] = "absent (nothing to migrate)"
        return

    # Sposta ogni item da projects/ a workspaces/
    for item in projects_dir.iterdir():
        target_path = workspaces_dir / item.name
        if target_path.exists() or target_path.is_symlink():
            skipped.append({"name": item.name, "reason": "exists in workspaces"})
            continue
        if not dry:
            if item.is_symlink():
                # ricrea symlink in workspaces/
                link_target = item.readlink() if hasattr(item, "readlink") else Path(str(item).replace("/projects/", "/workspaces/"))
                # Risolvi: leggi il path target del symlink
                try:
                    real_target = Path(__import__("os").readlink(str(item)))
                except OSError:
                    skipped.append({"name": item.name, "reason": "readlink failed"})
                    continue
                target_path.symlink_to(real_target)
                item.unlink()  # rimuovi vecchio symlink
                moved.append({"name": item.name, "kind": "external", "target": str(real_target)})
            else:
                item.rename(target_path)
                moved.append({"name": item.name, "kind": "internal_or_dir"})
        else:
            moved.append({"name": item.name, "kind": "would_move"})

    report["moved"] = moved
    report["skipped_in_move"] = skipped

    # Lascia projects/ come symlink a workspaces/ per back-compat
    if not dry and projects_dir.is_dir() and not any(projects_dir.iterdir()):
        projects_dir.rmdir()
        projects_dir.symlink_to("workspaces")
        report["projects_symlink"] = "created (projects → workspaces)"
    elif not dry:
        report["projects_symlink"] = "projects/ not empty after move (manual cleanup needed)"


def step_scaffold_hub_files(hub: Path, dry: bool, report: dict) -> None:
    """Crea <hub>/files/, data/, scripts/ con README placeholder."""
    created = []
    for sub in ("files", "data", "scripts"):
        d = hub / sub
        if d.exists():
            continue
        if not dry:
            d.mkdir(parents=True, exist_ok=True)
            readme = d / "README.md"
            readme.write_text(
                f"# {sub}/\n\n"
                f"Spazio di lavoro {sub} dell'hub (workspace principale di Anja).\n\n"
                f"Anja scrive qui per lavoro cross-workspace. I workspace specializzati\n"
                f"hanno la loro `{sub}/` separata in `workspaces/<nome>/.anjawiki/{sub}/`.\n",
                encoding="utf-8",
            )
        created.append(sub)
    report["hub_scaffold"] = created


def step_write_hub_meta(hub: Path, dry: bool, report: dict) -> None:
    """Crea/aggiorna <hub>/meta.yaml con kind: hub."""
    meta_path = hub / "meta.yaml"
    existing = read_yaml_simple(meta_path)
    if existing.get("kind") == "hub":
        report["hub_meta"] = "already kind=hub"
        return
    data = {
        "kind": "hub",
        "name": hub.name,
        "schema_version": "v22",
        "migrated": _today_iso(),
    }
    # Preserva field esistenti se ce ne sono
    existing.update(data)
    if not dry:
        write_yaml_simple(meta_path, existing)
    report["hub_meta"] = "written" if not dry else "would_write"


def step_mark_workspace_kinds(hub: Path, dry: bool, report: dict) -> None:
    """Per ogni workspace in workspaces/, crea marker `<name>.meta.yaml` con kind detection.

    External = symlink. Internal = dir reale.
    Mettiamo il meta accanto al symlink (non DENTRO il .anjawiki) perché external è linkato e
    non vogliamo toccare il progetto remoto.
    """
    ws_dir = hub / "workspaces"
    if not ws_dir.is_dir():
        report["workspace_kinds"] = "no workspaces/ dir"
        return
    marked = []
    for item in ws_dir.iterdir():
        if item.name.endswith(".meta.yaml"):
            continue
        kind = "external" if item.is_symlink() else "internal"
        marker = ws_dir / f"{item.name}.meta.yaml"
        if marker.is_file():
            marked.append({"name": item.name, "kind": kind, "status": "already_marked"})
            continue
        data = {
            "kind": kind,
            "name": item.name,
            "created": _today_iso(),
        }
        if not dry:
            write_yaml_simple(marker, data)
        marked.append({"name": item.name, "kind": kind, "status": "marked" if not dry else "would_mark"})
    report["workspace_kinds"] = marked


def main():
    ap = argparse.ArgumentParser(description="Migrazione hub a Fase 22 Workspace.")
    ap.add_argument("--hub", required=True, help="Path del hub (es. ~/Documents/TEST-HUB)")
    ap.add_argument("--dry-run", action="store_true", help="Mostra cosa cambierebbe senza scrivere")
    args = ap.parse_args()

    hub = Path(args.hub).expanduser().resolve()
    if not hub.is_dir():
        print(f"ERROR: hub path is not a directory: {hub}", file=sys.stderr)
        sys.exit(1)

    report = {"hub": str(hub), "dry_run": args.dry_run, "started": _today_iso()}

    try:
        step_create_workspaces_dir(hub, args.dry_run, report)
        step_move_projects_to_workspaces(hub, args.dry_run, report)
        step_scaffold_hub_files(hub, args.dry_run, report)
        step_write_hub_meta(hub, args.dry_run, report)
        step_mark_workspace_kinds(hub, args.dry_run, report)
        report["status"] = "ok"
    except Exception as e:
        import traceback
        report["status"] = "error"
        report["error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0 if report["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
