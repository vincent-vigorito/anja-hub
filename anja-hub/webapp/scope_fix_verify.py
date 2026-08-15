#!/usr/bin/env python3
"""Verifica del fix backend: normalize_workspace_scope risolve il mismatch project:/workspace:."""
from pathlib import os
import Path

import kanban_io
import goal_io

HUB = Path(os.environ.get("ANJA_HUB", ""))

print("== normalizzazione scope ==")
for c in ["project:demo-brand", "demo-brand", "workspace:demo-brand", "hub", "project:AnjaHub", None]:
    print(f"  {str(c):26} -> {kanban_io.normalize_workspace_scope(HUB, c)}")

print("\n== list_tasks (input scope -> normalizzato -> N card) ==")
for c in ["project:demo-brand", "demo-brand", "hub", "project:AnjaHub"]:
    s = kanban_io.normalize_workspace_scope(HUB, c)
    tasks = kanban_io.list_tasks(HUB, scope=s)
    ready = [t for t in tasks if t.get("status") == "ready"]
    print(f"  {str(c):20} -> {str(s):24} : {len(tasks)} card ({len(ready)} ready)")

print("\n== list_goals (input scope -> normalizzato -> N goal) ==")
for c in ["project:demo-brand", "hub"]:
    s = kanban_io.normalize_workspace_scope(HUB, c)
    try:
        goals = goal_io.list_goals(HUB, scope=s)
        print(f"  {str(c):20} -> {str(s):24} : {len(goals)} goal")
    except Exception as e:
        print(f"  {str(c):20} : err {type(e).__name__}: {e}")
