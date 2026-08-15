#!/usr/bin/env python3
"""
lint_hub.py — check cross-progetto del hub anja.

Output JSON con issues. The agent wraps with severity ordering and writes
the markdown report.

Check eseguiti:
  - cross-link-unknown-project (error): [[<project>/wiki/<page>]] dove project NON è nel registry
  - cross-link-broken (error): [[<project>/wiki/<page>]] dove page non esiste nel wiki del progetto
  - frontmatter-unknown-project (warning): cross/analysis/*.md ha `projects: [..., X, ...]` con X fuori dal registry
  - index-missing-entry (warning): cross/analysis/*.md non listato in cross/index.md
  - tag-variant (suggestion): heuristica su tag potenzialmente inconsistenti tra progetti (singular/plural, hyphen/underscore)
"""

import argparse
import json
import re
import sys
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
PROJECT_LINK_RE = re.compile(r"^([a-zA-Z0-9_\-]+)/wiki/([a-zA-Z0-9_\-]+)$")
WIKI_SUBDIRS = ("entities", "concepts", "sources", "analysis", "sessions")


def load_registry(hub_root: Path):
    path = hub_root / "config" / "projects.json"
    if not path.is_file():
        sys.exit(f"ERROR: registry not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    info = {}
    for key in ("title", "type"):
        mm = re.search(rf'^\s*{key}:\s*"?([^"\n]+?)"?\s*$', block, re.M)
        if mm:
            info[key] = mm.group(1)
    mm = re.search(r"^\s*projects:\s*\[([^\]]*)\]", block, re.M)
    if mm:
        raw = mm.group(1)
        info["projects"] = [t.strip().strip("\"'") for t in raw.split(",") if t.strip()]
    mm = re.search(r"^\s*tags:\s*\[([^\]]*)\]", block, re.M)
    if mm:
        raw = mm.group(1)
        info["tags"] = [t.strip().strip("\"'") for t in raw.split(",") if t.strip()]
    return info


def find_page_in_project(hub_root: Path, project_name: str, page_slug: str):
    """Search for a page in a project's wiki (root or subdirs). Returns Path or None."""
    base = hub_root / "projects" / project_name / "wiki"
    if not base.is_dir():
        return None
    candidate = base / f"{page_slug}.md"
    if candidate.is_file():
        return candidate
    for sub in WIKI_SUBDIRS:
        candidate = base / sub / f"{page_slug}.md"
        if candidate.is_file():
            return candidate
    return None


def check_cross_links(hub_root: Path, registry: dict) -> list:
    issues = []
    project_names = {p["name"] for p in registry["projects"]}
    cross_dir = hub_root / "cross"
    if not cross_dir.is_dir():
        return issues

    for md_file in cross_dir.rglob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        for match in WIKILINK_RE.findall(text):
            link = match.split("|")[0].split("#")[0].strip()
            m = PROJECT_LINK_RE.match(link)
            if not m:
                continue
            project_name = m.group(1)
            page_slug = m.group(2)
            rel = str(md_file.relative_to(hub_root))
            if project_name not in project_names:
                issues.append({
                    "severity": "error",
                    "type": "cross-link-unknown-project",
                    "page": rel,
                    "link": link,
                    "message": f"link [[{link}]] in '{rel}' references project '{project_name}' not in registry",
                })
                continue
            if find_page_in_project(hub_root, project_name, page_slug) is None:
                issues.append({
                    "severity": "error",
                    "type": "cross-link-broken",
                    "page": rel,
                    "link": link,
                    "message": f"link [[{link}]] in '{rel}' points to non-existent page in project '{project_name}'",
                })
    return issues


def check_frontmatter_projects(hub_root: Path, registry: dict) -> list:
    project_names = {p["name"] for p in registry["projects"]}
    issues = []
    analysis_dir = hub_root / "cross" / "analysis"
    if not analysis_dir.is_dir():
        return issues
    for f in analysis_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        for proj in fm.get("projects", []):
            if proj not in project_names:
                issues.append({
                    "severity": "warning",
                    "type": "frontmatter-unknown-project",
                    "page": str(f.relative_to(hub_root)),
                    "project": proj,
                    "message": f"frontmatter `projects:` in '{f.relative_to(hub_root)}' references unknown project '{proj}'",
                })
    return issues


def check_index_alignment(hub_root: Path) -> list:
    issues = []
    analysis_dir = hub_root / "cross" / "analysis"
    if not analysis_dir.is_dir():
        return issues
    analysis_slugs = {f.stem for f in analysis_dir.glob("*.md")}

    index_path = hub_root / "cross" / "index.md"
    if not index_path.is_file():
        return issues
    text = index_path.read_text(encoding="utf-8")
    listed = {l.split("|")[0].split("#")[0].strip() for l in WIKILINK_RE.findall(text)}

    for slug in analysis_slugs - listed:
        issues.append({
            "severity": "warning",
            "type": "index-missing-entry",
            "slug": slug,
            "message": f"cross/analysis/{slug}.md not listed in cross/index.md",
        })
    return issues


def check_tag_consistency(hub_root: Path, registry: dict) -> list:
    """Heuristic: tags differing only by hyphen/underscore/case are flagged."""
    all_tags = set()
    for p in registry["projects"]:
        wiki_dir = hub_root / "projects" / p["name"] / "wiki"
        if not wiki_dir.is_dir():
            continue
        for f in wiki_dir.rglob("*.md"):
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            for tag in fm.get("tags", []):
                all_tags.add(tag)

    issues = []
    seen = []
    for tag in sorted(all_tags):
        normalized = tag.replace("-", "").replace("_", "").lower()
        for prev_tag, prev_norm in seen:
            if prev_norm == normalized and prev_tag != tag:
                issues.append({
                    "severity": "suggestion",
                    "type": "tag-variant",
                    "tags": [prev_tag, tag],
                    "message": f"possible variant tags across projects: '{prev_tag}' vs '{tag}' — consider standardizing",
                })
                break
        else:
            seen.append((tag, normalized))
    return issues


def main() -> None:
    p = argparse.ArgumentParser(description="Lint cross-project for anja hub.")
    p.add_argument("--hub", required=True, help="path to hub directory")
    args = p.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        sys.exit(f"ERROR: hub not found: {hub}")

    registry = load_registry(hub)

    issues = []
    issues += check_cross_links(hub, registry)
    issues += check_frontmatter_projects(hub, registry)
    issues += check_index_alignment(hub)
    issues += check_tag_consistency(hub, registry)

    summary = {
        "hub": str(hub),
        "projects_count": len(registry["projects"]),
        "issues_total": len(issues),
        "by_severity": {
            "error": sum(1 for i in issues if i["severity"] == "error"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "suggestion": sum(1 for i in issues if i["severity"] == "suggestion"),
        },
        "issues": issues,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
