# System status (as of 2026-08-13, ~5:40pm ET)

There are two separate systems. They do not share code, credentials, or a
session — keep them mentally (and operationally) apart.

## 1. This repo (`raffallv/trading-news`) — Telegram alerts only

Scope from here on: **news/catalyst alerts to Telegram.** Nothing else gets
touched in this repo without an explicit ask.

- `catalyst_news.py brief` / `scan` — keyword-scored news catalysts (FDA,
  M&A, earnings, etc.) → Telegram. Runs via `.github/workflows/brief.yml`
  and `monitor.yml`. This is the original, primary feature.
- `catalyst_news.py finviz` — Finviz Elite "Momentum scanner" preset
  (`FINVIZ_FILTER`, cap $20M-$10B / AMEX-NASDAQ-NYSE / RVOL>3x / float
  1M-30M / day perf >5%) → Telegram. Runs via `finviz_scan.yml`.
- `catalyst_news.py dashboard` — Finviz screen (`DASHBOARD_FILTER`, cap
  $2M-$10B / price $1-$20 / RVOL>3x / float 1M-30M / change>=15%) → writes
  `docs/dashboard.html` + Telegram alerts. Runs via `dashboard.yml`.
  **GitHub Pages is not yet enabled** — needs a one-time manual step
  (Settings → Pages → Deploy from branch → main /docs) before the page is
  reachable at `https://raffallv.github.io/trading-news/dashboard.html`.

**Known issue:** Telegram sends have been failing consistently (15s read
timeouts against `api.telegram.org`) since ~4:30pm ET today, across ~30+
attempts from GitHub Actions. Likely self-inflicted flood-limiting from
heavy live testing in a short window — not a code bug. `send_telegram()`
already retries once and only marks a ticker "seen" after a confirmed
send, so nothing gets silently lost once Telegram recovers; failed sends
retry on the next scheduled run. Re-test with a single isolated send
after a cooldown to confirm.

## 2. iMac local dashboard — NOT in this repo, NOT tracked here

`http://rafals-imac.local:8787/momentum_dashboard.html` — a local server
on the user's iMac, reachable via Tailscale/mDNS. Built in a **different**
Claude session with direct access to that machine. This session has never
had access to it, has no record of its code, and cannot verify whether
it's currently running. If it needs work, go back to that session (or a
new Claude Code session run directly on the iMac).
