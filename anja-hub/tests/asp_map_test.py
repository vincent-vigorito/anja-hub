#!/usr/bin/env python3
"""asp_map_test.py — Fase 3 ASP: unit test del mapping messaggi→eventi.
Messaggi SDK finti (stessi nomi di classe) → _map_message, senza LLM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import claude_session as cs

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


def fake(cls_name, **attrs):
    return type(cls_name, (), attrs)()


handle = cs.SessionHandle.__new__(cs.SessionHandle)
handle.sdk_session_id = None
handle.pending_subagents = {}
handle.tasks = {}
peak = [0]


def events_of(msg):
    return cs._map_message(msg, handle, "haiku", peak)


# thinking → segnale senza contenuto
msg = fake("AssistantMessage", content=[fake("ThinkingBlock", thinking="...")])
evs = events_of(msg)
check("ThinkingBlock → thinking", any(e["type"] == "thinking" for e in evs))
check("thinking senza contenuto",
      all("content" not in e for e in evs if e["type"] == "thinking"))

# TodoWrite → todo.updated, niente tool_use
msg = fake("AssistantMessage", content=[fake(
    "ToolUseBlock", name="TodoWrite", id="t1",
    input={"todos": [{"content": "passo 1", "status": "completed"},
                     {"content": "passo 2", "status": "in_progress"}]})])
evs = events_of(msg)
todo = [e for e in evs if e["type"] == "todo.updated"]
check("TodoWrite → todo.updated", len(todo) == 1)
check("todo stati mappati", todo and todo[0]["todos"][1]["status"] == "in_progress")
check("TodoWrite niente tool_use", not any(e["type"] == "tool_use" for e in evs))

# TaskCreate/TaskUpdate (CLI ≥2.1.2xx) → todo.updated con lista accumulata
msg = fake("AssistantMessage", content=[
    fake("ToolUseBlock", name="TaskCreate", id="tc1",
         input={"subject": "passo A", "activeForm": "Facendo A"}),
    fake("ToolUseBlock", name="TaskCreate", id="tc2",
         input={"subject": "passo B", "activeForm": "Facendo B"})])
evs = events_of(msg)
todo = [e for e in evs if e["type"] == "todo.updated"]
check("TaskCreate → todo.updated", len(todo) == 2
      and [t["content"] for t in todo[-1]["todos"]] == ["passo A", "passo B"])
msg = fake("AssistantMessage", content=[fake(
    "ToolUseBlock", name="TaskUpdate", id="tu1",
    input={"taskId": "1", "status": "in_progress"})])
evs = events_of(msg)
todo = [e for e in evs if e["type"] == "todo.updated"]
check("TaskUpdate stato aggiornato", todo
      and todo[0]["todos"][0]["status"] == "in_progress"
      and todo[0]["todos"][1]["status"] == "pending")
check("Task* niente tool_use", not any(e["type"] == "tool_use" for e in evs))
handle.tasks.clear()

# Task → subagent.started + tracking; suo result → subagent.completed
msg = fake("AssistantMessage", content=[fake(
    "ToolUseBlock", name="Task", id="task-9",
    input={"description": "esplora il repo"})])
evs = events_of(msg)
check("Task → subagent.started",
      any(e["type"] == "subagent.started" and e["label"] == "esplora il repo"
          for e in evs))
check("subagent tracciato", "task-9" in handle.pending_subagents)

msg = fake("UserMessage", content=[fake(
    "ToolResultBlock", tool_use_id="task-9", is_error=False, content="ok")])
evs = events_of(msg)
check("result Task → subagent.completed",
      any(e["type"] == "subagent.completed" and not e["is_error"] for e in evs))
check("tracking ripulito", "task-9" not in handle.pending_subagents)

# tool_result con errore (non-subagent) → tool.result
msg = fake("UserMessage", content=[fake(
    "ToolResultBlock", tool_use_id="x1", is_error=True, content="boom")])
evs = events_of(msg)
check("tool error → tool.result",
      any(e["type"] == "tool.result" and e["is_error"] for e in evs))

# usage con context_window (serve all'auto-compact: senza degrada al fallback)
msg = fake("ResultMessage", usage={"input_tokens": 10, "output_tokens": 5})
evs = events_of(msg)
usage = [e for e in evs if e["type"] == "usage"]
check("usage ha context_window", usage
      and usage[0].get("context_window") == 200000, str(usage))

# tool_result ok non-subagent → silenzio (no rumore)
msg = fake("UserMessage", content=[fake(
    "ToolResultBlock", tool_use_id="x2", is_error=False, content="fine")])
check("tool ok → nessun evento", events_of(msg) == [])

# tool normale → tool_use invariato (compat)
msg = fake("AssistantMessage", content=[fake(
    "ToolUseBlock", name="Read", id="r1", input={"file_path": "/a"})])
evs = events_of(msg)
check("Read → tool_use legacy",
      any(e["type"] == "tool_use" and e["name"] == "Read" for e in evs))

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
