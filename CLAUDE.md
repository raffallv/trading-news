# trading-news — quick reference

Single-file Python bot (`catalyst_news.py`) that sends stock catalyst
alerts to Telegram, plus a live dashboard published via GitHub Pages.
Everything runs on GitHub Actions — nothing depends on any local machine
being on. See `STATUS.md` for the scope boundary vs. the separate iMac/
Tailscale dashboard (different system, different session, not tracked here).

## Modes (`python catalyst_news.py <mode>`)

| Mode | What it does | Workflow | Schedule |
|---|---|---|---|
| `brief` | Top-10 catalyst digest from news | `brief.yml` | 5:30am ET weekdays + Sun 6pm ET |
| `scan` | Keyword-scored breaking news (FDA, M&A, earnings, etc.) | `monitor.yml` | every 15 min, 4am-4pm ET, Mon-Fri |
| `finviz` | "Momentum scanner" Finviz preset (`FINVIZ_FILTER`) | `finviz_scan.yml` | **disabled** (schedule commented out by request — Dashboard Match covers this) |
| `dashboard` | Finviz screen (`DASHBOARD_FILTER`) → `docs/dashboard.html` + Telegram | `dashboard.yml` | every 5 min, 4am-4pm ET, Mon-Fri |
| `test-telegram` | Single connectivity ping, logs OK/FAILED + latency | `test_telegram.yml` | manual only |
| `test-alerts` | 2 clearly-fake alerts in real format, no Finviz call, no seen-cache writes | `test_fake_alerts.yml` | manual only |

`monitor`/`run_monitor()` (infinite loop) is legacy/unused — GitHub Actions cron replaced it.

**Manual (`workflow_dispatch`) runs bypass the trading-hours window** —
`in_trading_window()` checks `GITHUB_EVENT_NAME`. Scheduled/cron runs still
only execute 4am-4pm ET weekdays.

## Filters — READ THIS BEFORE TOUCHING MARKET CAP

Finviz's `cap_XtoY` custom market-cap filter is denominated in **billions**,
not millions. `cap_0.02to10` = $20M-$10B. Writing `cap_10to10000` assuming
millions actually means **>$10B** (nonsensical) — this exact bug shipped
twice in this project before being caught via a screenshot of the real
Finviz UI (market cap inputs explicitly labeled "B"). Always sanity-check
a new cap filter against that convention.

- `FINVIZ_FILTER` (Momentum Screen) — mirrors the user's real saved Finviz
  Elite preset: cap $20M-$10B, AMEX/NASDAQ/NYSE only, avg vol>300K,
  current vol>300K, float 1M-30M, price $1-$20, RVOL>3x, day perf >5%.
- `DASHBOARD_FILTER` — cap $2M-$10B, price $1-$20, RVOL>3x, avg vol>300K,
  float 1M-30M, day change ≥15%.
- These two overlap by design and can both fire for the same ticker —
  **confirmed as expected behavior, not a bug** (user chose to keep both
  separate rather than merge/dedup them).

## RVOL — do not confuse with Vol÷AvgVol

The `RVOL` shown in alerts/dashboard (`row["rel_volume"]`, `catalyst_news.py`)
is Finviz's own **Rel Volume** export column — pulled directly, never
computed locally from the `Vol` / `Avg Vol` fields also shown alongside it.
Those three are independent columns from the same Finviz row, not derived
from each other.

`Vol ÷ Avg Vol` (e.g. 2.45M / 2,376.28K ≈ 1.03x) answers "how does
today's volume-so-far compare to a full normal day." RVOL answers a
different question: "how does today's volume-so-far compare to what
*usually trades by this same clock time* on a normal day" (time-of-day
normalized). Early in the session that time-of-day baseline is a small
sliver of the full-day average, so RVOL reads much higher than the naive
Vol/AvgVol ratio — e.g. RVOL 20.62x alongside Vol/AvgVol≈1.03x is
expected, not a bug or a data error. Don't "fix" RVOL to match
Vol÷AvgVol if asked to investigate a discrepancy — recompute confirms the
raw fields are fine; explain the time-of-day-normalization instead.

Rough interpretation scale for this dashboard's tickers (low float
1M-30M, so extra prone to whipsaw at high RVOL): <1x quiet · 1-2x mildly
elevated · 2-3x worth a look · 3-5x real catalyst move (the filters'
`sh_relvol_o3` cutoff) · 5-10x strong, still liquid enough to trade ·
10x+ extreme, real but wide spreads/possible halts — treat as a spotlight
on where attention is, not a standalone buy signal, and remember the same
RVOL number is a bigger deal later in the day than right after the open.

## Reliability notes

- `send_telegram()` retries once on failure/timeout.
- `scan_finviz()` / `run_dashboard()` only mark a ticker "seen" (dedup)
  **after** a confirmed successful send — a failed send retries next cycle
  instead of being silently lost. `scan_once()` (news) does NOT follow this
  pattern; its `seen` set has different semantics (avoid re-scoring the
  same headline, not just alert-dedup) — don't "fix" it to match without
  being asked, it's intentional.
- Telegram's Bot API has anti-flood limits (~1 msg/sec sustained per chat).
  Heavy rapid manual testing can trigger silent throttling — connections
  accept, then read-timeout at 15s, looking exactly like an outage. It
  clears on its own after the bot goes quiet for ~15-20 min. Run
  `test-telegram` once (not repeatedly) to check before assuming a real
  outage.
- `current_et()` hardcodes UTC-4 — only exactly correct during EDT. Not
  fixed; out of scope unless asked.

## GitHub Pages

Enabled: Source = Deploy from branch, `main` / `/docs`. Live at
`https://raffallv.github.io/trading-news/dashboard.html`. Page
auto-refreshes every 5 min in-browser (`<meta refresh>`); the underlying
file is regenerated by `dashboard.yml` on the same cadence.

## Required repo secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `FINNHUB_API_KEY`,
`FINVIZ_AUTH_TOKEN` — Settings → Secrets and variables → Actions.

## Working on this repo (for future Claude sessions)

PRs here are **squash-merged**. If continuing work across multiple PRs on
the same long-lived branch, it accumulates stale pre-squash commits vs.
`main` and causes spurious merge conflicts. Before starting new work:

```
git fetch origin main -q
git checkout -B <branch> origin/main
```

then reapply/cherry-pick anything uncommitted, rather than building on top
of the branch's old history.
