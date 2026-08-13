# System status (as of 2026-08-13, ~6:35pm ET)

There are two separate systems. They do not share code, credentials, or a
session — keep them mentally (and operationally) apart.

## 1. This repo (`raffallv/trading-news`) — Telegram alerts + dashboard

Scope: **Telegram alerts and the GitHub Pages dashboard.** Nothing else
gets touched in this repo without an explicit ask.

- `catalyst_news.py brief` / `scan` — keyword-scored news catalysts (FDA,
  M&A, earnings, etc.) → Telegram. Runs via `.github/workflows/brief.yml`
  and `monitor.yml`. Original, primary feature. **Active.**
- `catalyst_news.py finviz` — Finviz Elite "Momentum scanner" preset
  (`FINVIZ_FILTER`) → Telegram. **Disabled by request** — its schedule in
  `finviz_scan.yml` is commented out (Dashboard Match covers this need
  now). `workflow_dispatch` still works for a manual run; uncomment the
  cron block to bring the schedule back.
- `catalyst_news.py dashboard` — Finviz screen (`DASHBOARD_FILTER`, cap
  $2M-$10B / price $1-$20 / RVOL>3x / avg vol>300K / float 1M-30M /
  change>=15%) → writes `docs/dashboard.html` + Telegram alerts. Runs via
  `dashboard.yml`, every 5 min during premarket/market hours. **Active.**
  GitHub Pages is enabled (Settings → Pages → main /docs) and live at
  `https://raffallv.github.io/trading-news/dashboard.html`.

**Telegram connectivity:** confirmed working (`test-telegram` returned
OK, 0.6s) after an earlier self-inflicted flood-limit from heavy same-day
testing cleared on its own. No open issue.

## 2. iMac local dashboard — NOT in this repo, NOT tracked here

`http://rafals-imac.local:8787/momentum_dashboard.html` — a local server
on the user's iMac, reachable via Tailscale/mDNS. Built in a **different**
Claude session with direct access to that machine. This session has never
had access to it, has no record of its code, and cannot verify whether
it's currently running. If it needs work, go back to that session (or a
new Claude Code session run directly on the iMac).
