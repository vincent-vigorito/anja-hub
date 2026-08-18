# Changelog

All notable changes to Anja Hub are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com); versions follow
[SemVer](https://semver.org) (0.x: public API may still change between minors).

## [Unreleased]

### Changed

- UI language is now fully English: web app (labels, hints, toasts, API
  error messages, notifications) and Telegram bot (commands menu, /help,
  acks, permission prompts). Agent prompts unchanged for now.

### Added

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
