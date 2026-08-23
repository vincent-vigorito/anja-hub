# Changelog

All notable changes to Anja Hub are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com); versions follow
[SemVer](https://semver.org) (0.x: public API may still change between minors).

## [Unreleased]

### Added

- **Event triggers for routines** (`trigger: webhook`): a routine can now fire
  on an inbound event, not just on cron. `POST /hooks/{name}` (public path,
  gated by a mandatory per-hook secret — `hook_secret` in the YAML, resolvable
  from `routines/.secrets.env` — or GitHub-style HMAC via `hook_hmac: github` /
  `X-Hub-Signature-256`) validates, rate-limits (default 6/min,
  `hook_rate_limit`), dedups identical payloads (120s) and queues a fire-file;
  the routines daemon picks it up within one poll cycle (≤30s) and spawns the
  run with the payload injected at `{{event}}` in the prompt — wrapped as
  untrusted data and inserted only *after* secret expansion, so a hostile
  payload cannot exfiltrate `{{VAR}}` secrets. `schedule` becomes optional for
  webhook routines (cron + webhook can coexist). Example template
  `webhook-example.yaml` in the hub skeleton.
- **Scheduled metrics refresh**: new `scripts/metrics_refresh_all.py` iterates
  every local project and runs the marketing collectors (GSC/GA/Ads/Merchant/
  WooCommerce/Meta/social) with the workspace vault + hub fallback, reporting a
  one-line summary; new hub routine template `metrics-refresh-nightly.yaml`
  (05:30, off by default) sends it to Telegram. Dashboards and the morning
  brief no longer depend on a manual "Refresh".

- **Grok Build subscription as a chat backend** (`provider=grok_cli`): uses the
  Grok Build / SuperGrok seat through the official `grok` CLI — the CLI *is* the
  agent (native tools + the MCP servers of the workspace folder, trusted via
  `--trust`), anja spawns `grok -p --output-format streaming-json` per turn with
  the composed context as `--rules`, maps the stream (text, thinking, tool_use
  with `use_tool` → `mcp__<server>__<tool>`, one aggregated usage with the
  seat's real cost) and resumes Grok's own session on the next turn (`-r`; a
  stale or foreign id falls back to a fresh session). Settings → Providers has
  a **Grok Build** card (CLI detected? signed in as? models on the seat) with a
  **device-code sign-in** that works on a headless host, re-sign-in and sign-out;
  the model picker gets a "Grok Build (subscription)" group (models from
  `~/.grok/models_cache.json`, effort low/medium/high); Telegram `/provider`
  offers it. In workspace/agent scopes the agent is complete; on the **hub root**
  it runs **restricted** by default (`ANJA_GROK_CLI_HUB=restricted|full|off`): shell,
  edits/writes, subagents/workflows removed from the toolset, and secret files
  (`**/*.env`, tokens, `backup.key`, connectors) denied via `--deny` in every scope.
  Child env is an allowlist (`ANJA_JOURNAL=0`, Claude Code compat scanning off, no
  hub secrets). Distinct from `xai` (API key,
  unchanged). New modules `webapp/grok_oauth.py`, `webapp/grok_cli.py`, endpoints
  `/api/grok-oauth/*`. Requires grok CLI ≥ 1.0.5 and anjadev ≥ 0.24.
- **MCP tool names are flat on the wire** (`kanban_show`, `office_to_pdf`):
  Grok Build (and OpenAI-style function calling) hide tools whose name contains a
  dot, so `anja_hub_runtime`, `anja_office`, `anja_hub_ops`, `anja_images`,
  `anja_videos` now list underscore names and accept both forms in `tools/call`.
  No change for Claude Code (it already showed `mcp__anja_hub_runtime__kanban_show`).

### Fixed

- Routines daemon under systemd (user) ran with the stock
  `PATH=/usr/local/sbin:...:/usr/bin`, which lacks `~/.local/bin` — where the
  CLIs routines rely on live (`anja-cli`, `giv`, `grok`). First live run of
  `wiki-steward-nightly` failed with "script not found" because
  `command -v anja-cli` resolved to nothing. The daemon now prepends
  `~/.local/bin` to `PATH` at startup, and the nightly routine prompt falls
  back to `$HOME/.local/bin/anja-cli` explicitly.
- Telegram `/project <ws>` / `/agent <name>` did not survive the next turn: the
  scope was saved as `scope: "project:<ws>"` but the dispatcher read a
  `scope_project` key nobody persisted, so every message fell back to the hub
  and re-saved `scope: "hub"`. The dispatcher now derives the scope from `scope`
  (`_tg_scope_parts`) and the post-turn save keeps it. `/provider grok_cli` also
  moves a leftover non-Grok model (e.g. `opus`) to the seat default.
- Web chat usage/cost was recorded only if the WebSocket reader saw the `usage`
  event before the stream completed — short turns could lose their cost row.
  Recording moved to the stream drainer (once per event), tagged with the chat
  scope.

### Changed

- UI language is now fully English: web app (labels, hints, toasts, API
  error messages, notifications) and Telegram bot (commands menu, /help,
  acks, permission prompts). Agent prompts unchanged for now.

- **anjadev core split** (requires anjadev ≥ 0.21): the agents' work plane —
  `agent.list`/`agent.delegate`, `task.*`, `workspace.*`, `kanban.*`,
  `goal.*`, `pp.*` (28 tools, same names) — moved from the anjadev plugin into
  the new `anja_hub_runtime` MCP server (`anja-hub/scripts/mcp_hub_runtime.py`),
  which imports the webapp by position (no more `ANJA_HUB_WEBAPP` guessing).
  `anja_memory` (anjadev) is back to a pure CLI plugin: memory, sessions,
  soul/user, skills, wiki, roadmap, code, graph. Blueprint scaffold now writes
  **two** servers per workspace (`anja_memory` core + `anja_hub_runtime`
  planning; leads also get `agents`); `init_hub.py` registers both at hub
  level; the MCP scoper keyword map routes kanban/goals/@mentions/tasks/pp to
  the real `anja_hub_runtime` entry (the old logical names never matched an
  entry and were silently dropped). **Existing hubs**: run
  `python3 anja-hub/scripts/migrate_memory_hub_split.py --hub <hub> [--dry-run]`
  *before* updating the plugin — it rewrites every `.mcp.json` (hub, workspace,
  agent dirs) and lead configs, idempotently.

### Added

- **Wiki steward nightly routine** (requires anjadev ≥ 0.23): the hub is a
  consumer of the anjadev steward — `anja-hub/scripts/wiki_steward_nightly.py`
  runs `steward.py --apply` on the hub wiki and on every workspace with
  `.anjawiki/` (10-minute budget, decision-trail record `actor=steward`), and the
  new hub-skeleton routine `wiki-steward-nightly` (04:15, after dreaming at 04:00,
  disabled by default) reports the one-line summary on Telegram. Dreaming = who
  the user is; steward = what the repo knows.
- **Telegram: link a chat from the UI**: Settings → Integrations → Telegram →
  *Link a chat* generates a one-time code (10 min) with a `t.me/<bot>?start=…`
  deep link; the chat that opens it (or sends `/link <code>`) is added to the
  allow-list automatically — no more copying chat_ids around. Manual
  allow-list still works.
- **Telegram: inline buttons for permissions and plans**: 🔐 permission
  requests come with ✅ Allow · ✅ Always · 🚫 Deny buttons and 📋 plan
  proposals with ✅ Approve · 🔄 Replan (plus a plan excerpt). Buttons carry
  the request id, so they resolve exactly that request — including requests
  raised by **web UI** sessions pushed to the notification chat — and the
  message is edited afterwards (buttons removed, outcome + who decided).
  Text commands `/allow` `/deny` `/approve` `/replan` keep working.
- **Google OAuth client from the UI**: Settings → Integrations guides the
  one-time Cloud Console setup (redirect URI to copy, APIs to enable) and
  accepts the client JSON upload — no filesystem access needed. Workspaces
  only do the per-account *Connect Google*.
- **WooCommerce orders — real sales data**: `woo_collect` reads paid orders
  via `wc/v3` with the existing WP Application Password (no extra keys) into
  `wc_orders_daily` / `wc_order_products` / `wc_orders`; new **Sales** tab
  (revenue, orders, AOV, new customers, net revenue, cash ROAS = orders
  revenue / ads spend, B2B share, 90d chart, top products, regions, payment
  methods); agent tools `wc_sales_report` / `wc_orders`; blueprint lead/analyst
  get ads + woo tools. GA4 was under-counting orders by ~40% on a real shop.
- **Ads tab rebuilt on native data**: 8 KPI cards (spend, conversions, value,
  ROAS, CPA, CTR, CPC, clicks) with deltas, campaigns table with source badge,
  and a **Search terms** panel ("wasted spend" = queries that cost without
  converting → negative-keyword candidates); `ads_terms` table fed by the
  collector (28-day snapshot).
- **Native Google Ads API** (GAQL, v22): `google_ads_collect` writes real
  spend/impressions/clicks/conversions per campaign to `ads_daily` (prefix
  `gads:`), replacing the GA4 estimate when a developer token is configured;
  `ads_check` / `ads_report` agent tools (campaign, ad_group, keyword,
  search_term, daily levels); `adwords` OAuth scope; hub-level "Google Ads
  API" connector group. README section on Google connectors setup.
- **Claude subscription sign-in from the UI**: Settings → Providers now shows
  the real CLI auth state (`claude auth status`, not just credential-file
  presence) and lets you sign in without a shell on the host — opens the
  OAuth page, you paste the code back. Live SDK sessions are recycled after
  login. Fixes the "OAuth session expired" dead end on remote hubs.
- **Blueprint authoring (Forge, step 1)**: agents can now design and create
  new workspace blueprints in `<hub>/blueprints/` — `blueprint-authoring`
  skill documents the full format, and `POST /api/blueprints/{name}/validate`
  runs a deterministic schema check (pod/agents/vault/routines) before
  instantiating. Validator covered by `tests/blueprint_validate_test.py`.

### Fixed

- Marketing agents were reading a stale legacy Google token
  (`config/connectors/gsc-token.json`) instead of the one written by
  *Connect Google* — new scopes (adwords, content) were invisible to
  `ads_*`/`merchant_*` tools right after authorizing. Token lookup is now
  workspace → hub → legacy; `marketing_status` reports token scopes.
- Google Ads client: tries the manager (MCC) header and direct access,
  remembers the working one; `ads_check` probes the customer for real.
- Statistics refresh now resolves hub-level shared keys (e.g. the Google Ads
  developer token) — previously only workspace vault values were passed.
- Connectors: per-field "✓ Connected / Not configured" badges and an honest
  group counter ("1 of 7 configured") for all-optional groups like image
  generation keys — previously a single key showed as "connected".
- Hub home / sidebar "+ Add" buttons now open the blueprint Marketplace
  instead of a legacy alert stub.

## [0.9.0] — 2026-08-15

First public release 🎉 — Anja Hub goes fully open source under MIT
(the Anja name and Mannaz logo remain trademarks, see TRADEMARK.md).

### Added

- **Mission Control** web app: multi-pane chat (Claude subscription via Agent
  SDK, OpenAI, Gemini, xAI, OpenRouter…), workspace switcher, memory
  inspector, media gallery, kanban, goals, cost tracking, decision trail,
  self-health monitoring.
- **Workspace blueprints**: scaffold a complete vertical workspace (agent pod,
  routines, connector schema, editorial templates) in one command; starter
  blueprint `marketing-site`; private blueprints loadable from
  `<hub>/blueprints/` with precedence over built-ins.
- **Telegram bot**: full agent sessions from the phone — steering, permission
  control (`/allow`, `/mode` with per-channel default), multi-thread
  conversations, voice replies (STT/TTS), generated media delivered as
  photos/videos in chat.
- **Routines daemon**: cron-like YAML routines with email/Slack/Google
  Chat/wiki/file/webhook action handlers.
- **Marketing integrations**: Google Search Console, GA4, Google Ads,
  Merchant Center (Merchant API v1), Meta ads + organic insights, WordPress —
  per-workspace SQLite metrics with dashboards and content audit scoring.
- **Media generation** via the `giv` CLI on Gemini, OpenAI, xAI, OpenRouter
  (Seedream/Seedance/Veo), with unified model catalog and per-workspace keys.
- **Research**: web search skills (DuckDuckGo free, SerpAPI, Google grounding
  via Gemini API) and Gemini Deep Research (async cited reports with UI
  section and notifications).
- **Multi-user Concierge mode**: login, owner/admin/member roles,
  per-workspace membership — free for everyone (no license gating).
- **Security hardening**: encrypted secrets vault (Fernet), CSRF/SSRF/path
  traversal defenses, authorization gates on mutating endpoints, rate-limited
  login, 25-check security gate suite.
- **Backup & DR**: encrypted snapshots with retention, memory time-machine
  with surgical undo, migration runner.
