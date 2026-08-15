# Changelog

All notable changes to Anja Hub are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com); versions follow
[SemVer](https://semver.org) (0.x: public API may still change between minors).

## [Unreleased]

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
