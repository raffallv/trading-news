#!/usr/bin/env python3
"""
Catalyst News Aggregator -> Telegram
Sources: Stocktwits (trending), Yahoo Finance RSS, Finnhub (news + quotes),
         Finviz Elite screener export (momentum scan)

Modes:
  python3 catalyst_news.py brief  -> Top-10 catalyst brief
  python3 catalyst_news.py scan   -> Single breaking-news scan (GitHub Actions)
  python3 catalyst_news.py finviz -> Single Finviz momentum screen (GitHub Actions)
"""

import os
import re
import csv
import sys
import json
import time
import html
import logging
import requests
import xml.etree.ElementTree as ET
from io import StringIO
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ---------------- CONFIG ----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
FINNHUB_API_KEY    = os.environ.get("FINNHUB_API_KEY", "")
FINVIZ_AUTH_TOKEN  = os.environ.get("FINVIZ_AUTH_TOKEN", "")

MONITOR_INTERVAL_SEC = 600
MONITOR_START_HOUR_ET = 4
MONITOR_END_HOUR_ET   = 16
MIN_BREAKING_SCORE    = 8
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")
FINVIZ_SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_finviz.json")
DASHBOARD_SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_dashboard.json")
DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "dashboard.html")

# Momentum screen — mirrors the "Momentum scanner" preset saved in Finviz Elite:
# $20M-$10B cap, AMEX/NASDAQ/NYSE only, $1-$20 price, RVOL>3x,
# avg vol>300K, current vol>300K, float 1M-30M, day performance >5%
FINVIZ_FILTER = "cap_0.02to10,exch_amex|nasd|nyse,sh_avgvol_o300,sh_curvol_o300,sh_float_1to30x,sh_price_1to20,sh_relvol_o3,ta_perf_d5o"

# Dashboard screen: $2M-$10B cap, $1-$20 price, RVOL>3x, avg vol>300K,
# float 1M-30M, day change >=15%. (cap_0.002to10 = $2M-$10B in Finviz's
# billions-denominated custom cap filter — cap_10to10000 as typed would
# mean >$10B, the same unit bug found and fixed in FINVIZ_FILTER above.)
DASHBOARD_FILTER = "cap_0.002to10,sh_price_o1,sh_price_u20,sh_relvol_o3,sh_avgvol_o300,sh_float_o1,sh_float_u30,ta_change_u15"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("catalyst")

# ---------------- CATALYST SCORING ----------------
CATALYST_PATTERNS = [
    (r"\bfda approv", 10, "FDA APPROVAL"),
    (r"\b(acquire[ds]?|acquisition|merger|to be acquired|buyout|takeover)\b", 10, "M&A"),
    (r"\bphase (3|iii).{0,40}(positive|met|success|topline)", 9, "PH3 DATA+"),
    (r"\b(breakthrough (therapy|designation))\b", 9, "FDA BTD"),
    (r"\bfda (clearance|510\(k\))", 8, "FDA CLEARANCE"),
    (r"\bphase (2|ii).{0,40}(positive|met|success|topline)", 8, "PH2 DATA+"),
    (r"\b(wins?|awarded|secures?|receives?).{0,50}(contract|order|deal)\b", 8, "CONTRACT WIN"),
    (r"\bpartnership|collaborat(es|ion) with\b", 7, "PARTNERSHIP"),
    (r"\b(earnings|revenue|eps).{0,40}(beat|record|surge|tops)", 7, "EARNINGS BEAT"),
    (r"\bguidance (raise[ds]?|boost)", 7, "GUIDANCE UP"),
    (r"\b(short squeeze|squeeze)\b", 6, "SQUEEZE CHATTER"),
    (r"\bstock (split|dividend)\b", 6, "SPLIT/DIV"),
    (r"\bbuyback|share repurchase\b", 6, "BUYBACK"),
    (r"\buplist(ing)?\b", 6, "UPLISTING"),
    (r"\bpatent (grant|issued|approv)", 6, "PATENT"),
    (r"\b(surge[ds]?|soar(s|ed)?|jumps?|spikes?|rockets?)\b", 5, "MOMENTUM"),
    (r"\binsider (buy|purchas)", 5, "INSIDER BUY"),
    (r"\bupgrade[ds]?\b", 4, "ANALYST UPGRADE"),
    (r"\bnew (52-week|all-time) high\b", 4, "NEW HIGH"),
    (r"\boffering|dilution|warrants?\b", 5, "OFFERING/DILUTION (warn)"),
    (r"\bcrl|complete response letter|fda reject", 7, "FDA REJECT (warn)"),
    (r"\b(halt(ed)?|investigation|sec charges|fraud|delist)", 6, "RED FLAG (warn)"),
    (r"\bbankrupt|chapter 11\b", 6, "BANKRUPTCY (warn)"),
]

TICKER_BLACKLIST = {
    "A", "I", "AT", "ON", "IT", "BE", "GO", "SO", "UP", "US", "AM", "PM", "CEO", "CFO",
    "IPO", "SEC", "FDA", "ETF", "NYSE", "AI", "EV", "USA", "GDP", "CPI", "FED", "Q", "EPS",
    "THE", "AND", "FOR", "NEW", "ALL", "TOP", "NOW", "OUT", "ITS", "HAS", "CAN", "ET", "EST",
}


def score_headline(text):
    t = text.lower()
    score = 0
    labels = []
    for pattern, pts, label in CATALYST_PATTERNS:
        if re.search(pattern, t):
            score += pts
            labels.append(label)
    return score, labels


# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing")
        return False
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    chunks = [msg[i:i + 4000] for i in range(0, len(msg), 4000)]
    ok = True
    for chunk in chunks:
        chunk_sent = False
        for attempt in range(1, 3):
            try:
                r = requests.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }, timeout=15)
                if r.status_code == 200:
                    chunk_sent = True
                    break
                log.error("Telegram error %s: %s", r.status_code, r.text[:200])
            except Exception as e:
                log.error("Telegram send failed (attempt %d/2): %s", attempt, e)
        if not chunk_sent:
            ok = False
    return ok


# ---------------- SOURCES ----------------
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def get_stocktwits_trending():
    out = {}
    try:
        r = requests.get("https://api.stocktwits.com/api/2/trending/symbols.json",
                         headers=UA, timeout=15)
        if r.status_code == 200:
            for s in r.json().get("symbols", []):
                sym = s.get("symbol", "")
                if sym and "." not in sym:
                    out[sym] = s.get("watchlist_count", 0)
    except Exception as e:
        log.warning("Stocktwits trending failed: %s", e)
    return out


def get_yahoo_rss(symbol=None):
    items = []
    if symbol:
        url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=" + symbol + "&region=US&lang=en-US"
    else:
        url = "https://finance.yahoo.com/news/rssindex"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub = item.findtext("pubDate") or ""
                if title:
                    items.append({"title": html.unescape(title), "link": link,
                                  "published": pub, "source": "Yahoo"})
    except Exception as e:
        log.warning("Yahoo RSS failed (%s): %s", symbol or "general", e)
    return items


def get_finnhub_general_news():
    items = []
    if not FINNHUB_API_KEY:
        return items
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": FINNHUB_API_KEY}, timeout=15)
        if r.status_code == 200:
            for n in r.json()[:60]:
                items.append({
                    "title": n.get("headline", ""),
                    "link": n.get("url", ""),
                    "source": n.get("source", "Finnhub"),
                    "related": n.get("related", ""),
                })
    except Exception as e:
        log.warning("Finnhub news failed: %s", e)
    return items


def get_finnhub_company_news(symbol, days=2):
    items = []
    if not FINNHUB_API_KEY:
        return items
    today = datetime.now(timezone.utc).date()
    try:
        r = requests.get("https://finnhub.io/api/v1/company-news", params={
            "symbol": symbol,
            "from": (today - timedelta(days=days)).isoformat(),
            "to": today.isoformat(),
            "token": FINNHUB_API_KEY,
        }, timeout=15)
        if r.status_code == 200:
            for n in r.json()[:10]:
                items.append({
                    "title": n.get("headline", ""),
                    "link": n.get("url", ""),
                    "source": n.get("source", "Finnhub"),
                })
    except Exception:
        pass
    return items


def get_finnhub_quote(symbol):
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get("https://finnhub.io/api/v1/quote",
                         params={"symbol": symbol, "token": FINNHUB_API_KEY}, timeout=10)
        if r.status_code == 200:
            q = r.json()
            return {"price": q.get("c"), "pct": q.get("dp")}
    except Exception:
        pass
    return None


def extract_tickers(text, related=""):
    tickers = set()
    if related:
        for t in related.split(","):
            t = t.strip().upper()
            if t and t not in TICKER_BLACKLIST and 1 <= len(t) <= 5:
                tickers.add(t)
    for m in re.findall(r"\$([A-Za-z]{1,5})\b", text):
        t = m.upper()
        if t not in TICKER_BLACKLIST:
            tickers.add(t)
    for m in re.findall(r"\((?:NASDAQ|NYSE|AMEX|OTC)[:\s]+([A-Z]{1,5})\)", text, re.I):
        tickers.add(m.upper())
    return tickers


# ---------------- BRIEF MODE ----------------
def run_brief():
    now_et = datetime.now(timezone(timedelta(hours=-4)))
    is_sunday = now_et.weekday() == 6
    log.info("Building brief...")

    all_news = get_finnhub_general_news() + get_yahoo_rss()
    trending = get_stocktwits_trending()

    ticker_data = defaultdict(lambda: {"score": 0, "labels": set(), "headlines": [], "st_rank": 0})

    for item in all_news:
        s, labels = score_headline(item["title"])
        if s <= 0:
            continue
        tickers = extract_tickers(item["title"], item.get("related", ""))
        for t in tickers:
            d = ticker_data[t]
            d["score"] += s
            d["labels"].update(labels)
            if len(d["headlines"]) < 3:
                d["headlines"].append(item)

    for sym in list(trending.keys())[:15]:
        for item in get_finnhub_company_news(sym) + get_yahoo_rss(sym)[:5]:
            s, labels = score_headline(item["title"])
            if s > 0:
                d = ticker_data[sym]
                d["score"] += s
                d["labels"].update(labels)
                if len(d["headlines"]) < 3:
                    d["headlines"].append(item)
        if sym in ticker_data:
            ticker_data[sym]["score"] += 3
            ticker_data[sym]["st_rank"] = trending[sym]

    ranked = sorted(ticker_data.items(), key=lambda kv: kv[1]["score"], reverse=True)[:10]

    if not ranked:
        send_telegram("📰 <b>Catalyst Brief</b>\n\nNo strong catalysts found in overnight news. Quiet session setup.")
        return

    title = "🗓 <b>SUNDAY PRE-WEEK CATALYST BRIEF</b>" if is_sunday else "☀️ <b>MORNING CATALYST BRIEF — " + now_et.strftime("%a %b %d") + "</b>"
    lines = [title, ""]

    for i, (sym, d) in enumerate(ranked, 1):
        quote = get_finnhub_quote(sym)
        px = ""
        if quote and quote.get("price"):
            pct = quote.get("pct") or 0
            arrow = "🟢" if pct >= 0 else "🔴"
            px = " | $" + format(quote["price"], ".2f") + " " + arrow + format(pct, "+.1f") + "%"
        labels = " · ".join(sorted(d["labels"]))[:80]
        lines.append("<b>" + str(i) + ". $" + sym + "</b> (score " + str(d["score"]) + ")" + px)
        lines.append("   " + labels)
        for h in d["headlines"][:2]:
            lines.append("   • " + h["title"][:110])
        if d["st_rank"]:
            lines.append("   👀 Trending on Stocktwits")
        lines.append("")

    lines.append("⚠️ Scores are keyword-based — verify catalysts before trading.")
    send_telegram("\n".join(lines))
    log.info("Brief sent: %d tickers", len(ranked))


# ---------------- SCAN MODE ----------------
def current_et():
    return datetime.now(timezone(timedelta(hours=-4)))


def in_trading_window():
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True  # manual runs are explicit intent — always execute
    t = current_et()
    return t.weekday() < 5 and MONITOR_START_HOUR_ET <= t.hour < MONITOR_END_HOUR_ET


def load_seen(path=SEEN_FILE):
    try:
        with open(path) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen, path=SEEN_FILE):
    try:
        with open(path, "w") as f:
            json.dump(list(seen)[-2000:], f)
    except Exception as e:
        log.warning("Could not save seen file (%s): %s", path, e)


def scan_once():
    if not in_trading_window():
        log.info("Outside market window — skipping scan")
        return

    seen = load_seen()
    first_run = len(seen) == 0
    try:
        news = get_finnhub_general_news() + get_yahoo_rss()
        alerts = []
        for item in news:
            key = item["title"][:120]
            if key in seen:
                continue
            seen.add(key)
            if first_run:
                continue
            s, labels = score_headline(item["title"])
            if s >= MIN_BREAKING_SCORE:
                tickers = extract_tickers(item["title"], item.get("related", ""))
                tick_str = " ".join("$" + t for t in sorted(tickers)) or "—"
                alerts.append(
                    "🚨 <b>BREAKING</b> (" + " · ".join(labels) + ")\n"
                    + tick_str + "\n" + item["title"][:200] + "\n" + item.get("link", "")
                )
        for a in alerts[:5]:
            send_telegram(a)
        log.info("Scan done: %d alerts", len(alerts))
        save_seen(seen)
    except Exception as e:
        log.error("Scan error: %s", e)


# ---------------- FINVIZ MOMENTUM SCREEN ----------------
# Column names we look for in the export header, in priority order per field.
# Finviz's own header labels are used instead of hardcoded column IDs so a
# wrong/renumbered column ID degrades to "n/a" instead of silently mislabeling data.
FINVIZ_FIELD_ALIASES = {
    "ticker": ["Ticker"],
    "price": ["Price"],
    "change": ["Change"],
    "volume": ["Volume"],
    "avg_volume": ["Avg Volume", "Average Volume"],
    "rel_volume": ["Rel Volume", "Relative Volume"],
    "float": ["Float", "Shs Float", "Shares Float"],
    "market_cap": ["Market Cap"],
}


def get_finviz_screener(filter_str=None):
    if not FINVIZ_AUTH_TOKEN:
        log.error("FINVIZ_AUTH_TOKEN missing — skipping Finviz screen")
        return []
    cols = ",".join(str(i) for i in range(71))
    url = "https://elite.finviz.com/export.ashx"
    try:
        r = requests.get(url, params={
            "v": "152",
            "f": filter_str or FINVIZ_FILTER,
            "c": cols,
            "auth": FINVIZ_AUTH_TOKEN,
        }, headers=UA, timeout=20)
        if r.status_code != 200:
            log.error("Finviz export error %s: %s", r.status_code, r.text[:200])
            return []
        reader = csv.DictReader(StringIO(r.text))
        if not reader.fieldnames:
            log.error("Finviz export returned no columns")
            return []
        header_map = {}
        for field, aliases in FINVIZ_FIELD_ALIASES.items():
            for alias in aliases:
                match = next((h for h in reader.fieldnames if h.strip().lower() == alias.lower()), None)
                if match:
                    header_map[field] = match
                    break
            else:
                log.warning("Finviz export missing expected column: %s", field)
        rows = []
        for row in reader:
            rows.append({field: row.get(col, "n/a") for field, col in header_map.items()})
        return rows
    except Exception as e:
        log.error("Finviz screener fetch failed: %s", e)
        return []


def scan_finviz():
    if not in_trading_window():
        log.info("Outside market window — skipping Finviz screen")
        return

    seen = load_seen(FINVIZ_SEEN_FILE)
    today_key = current_et().strftime("%Y-%m-%d")
    try:
        rows = get_finviz_screener()
        sent = 0
        for row in rows[:10]:
            ticker = row.get("ticker")
            if not ticker:
                continue
            key = ticker + "|" + today_key
            if key in seen:
                continue
            msg = (
                "🎯 <b>MOMENTUM SCREEN</b> — RVOL>3x, float 1M-30M, day perf >5%\n"
                "$" + ticker + " | $" + str(row.get("price", "n/a"))
                + " (" + str(row.get("change", "n/a")) + ")\n"
                "RVOL: " + str(row.get("rel_volume", "n/a"))
                + " · Vol: " + format_raw_count(row.get("volume", "n/a"))
                + " · Avg Vol: " + format_avg_vol(row.get("avg_volume", "n/a")) + "\n"
                "Float: " + format_millions(row.get("float", "n/a"))
                + " · Mkt Cap: " + format_millions(row.get("market_cap", "n/a"))
            )
            # Only mark as seen once the alert actually sends — a failed/timed-out
            # send must not permanently suppress that ticker for the trading day.
            if send_telegram(msg):
                seen.add(key)
                sent += 1
        log.info("Finviz screen done: %d matches, %d new alerts", len(rows), sent)
        save_seen(seen, FINVIZ_SEEN_FILE)
    except Exception as e:
        log.error("Finviz scan error: %s", e)


# ---------------- LIVE DASHBOARD ----------------
def format_avg_vol(value):
    # Finviz's export gives this column pre-divided by 1,000 (matching the
    # "524.52K" style shown on-site) but without the K suffix — add it back.
    try:
        float(value)
        return str(value) + "K"
    except (TypeError, ValueError):
        return str(value)


def format_raw_count(value):
    # Volume comes as a bare share count (e.g. "3393445") — scale to K/M/B.
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return str(value)
    if n >= 1_000_000_000:
        return "%.2fB" % (n / 1_000_000_000)
    if n >= 1_000_000:
        return "%.2fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.2fK" % (n / 1_000)
    return "%.0f" % n


def format_millions(value):
    # Float and Market Cap come pre-expressed in millions (e.g. "336.78" =
    # $336.78M) — bump to B above 1000 and add the missing suffix.
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n >= 1000:
        return "%.2fB" % (n / 1000)
    return "%.2fM" % n


def render_dashboard_html(rows, generated_at):
    cells = []
    for r in rows:
        cells.append(
            "<tr><td>$" + html.escape(str(r.get("ticker", "n/a"))) + "</td>"
            "<td>" + html.escape(str(r.get("price", "n/a"))) + "</td>"
            "<td>" + html.escape(str(r.get("change", "n/a"))) + "</td>"
            "<td>" + html.escape(str(r.get("rel_volume", "n/a"))) + "</td>"
            "<td>" + html.escape(format_raw_count(r.get("volume", "n/a"))) + "</td>"
            "<td>" + html.escape(format_avg_vol(r.get("avg_volume", "n/a"))) + "</td>"
            "<td>" + html.escape(format_millions(r.get("float", "n/a"))) + "</td>"
            "<td>" + html.escape(format_millions(r.get("market_cap", "n/a"))) + "</td></tr>"
        )
    rows_html = "\n".join(cells) if cells else "<tr><td colspan='8'>No matches right now.</td></tr>"
    return """<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300">
<title>Momentum Dashboard</title>
<style>
body{font-family:-apple-system,Arial,sans-serif;background:#0a1628;color:#dfe8f5;padding:24px}
h1{color:#f0f5fc}
table{border-collapse:collapse;width:100%;max-width:960px}
th,td{padding:10px 14px;text-align:left;border-bottom:1px solid #1e3252}
th{background:#132b4d;color:#8fc1f0;font-weight:600;letter-spacing:.02em}
td{background:#0d1f38}
tr:hover td{background:#152c4d}
.meta{color:#7a93b8;margin-bottom:16px}
</style></head>
<body>
<h1>Momentum Screener Dashboard</h1>
<div class="meta">Filter: cap $2M-$10B · price $1-$20 · RVOL&gt;3x · avg vol&gt;300K · float 1M-30M · day change &ge;15%<br>
Last updated: """ + html.escape(generated_at) + """ · refreshes every 5 minutes</div>
<table>
<tr><th>Ticker</th><th>Price</th><th>Change</th><th>RVOL</th><th>Volume</th><th>Avg Vol</th><th>Float</th><th>Mkt Cap</th></tr>
""" + rows_html + """
</table>
</body></html>
"""


def run_dashboard():
    seen = load_seen(DASHBOARD_SEEN_FILE)
    today_key = current_et().strftime("%Y-%m-%d")
    rows = get_finviz_screener(DASHBOARD_FILTER)

    os.makedirs(os.path.dirname(DASHBOARD_HTML_PATH), exist_ok=True)
    generated_at = current_et().strftime("%a %b %d, %Y %I:%M:%S %p ET")
    with open(DASHBOARD_HTML_PATH, "w") as f:
        f.write(render_dashboard_html(rows, generated_at))
    log.info("Dashboard written: %d rows", len(rows))

    sent = 0
    for row in rows[:10]:
        ticker = row.get("ticker")
        if not ticker:
            continue
        key = ticker + "|" + today_key
        if key in seen:
            continue
        msg = (
            "📊 <b>DASHBOARD MATCH</b> — cap $2M-$10B, RVOL>3x, float 1M-30M, change>=15%\n"
            "$" + ticker + " | $" + str(row.get("price", "n/a"))
            + " (" + str(row.get("change", "n/a")) + ")\n"
            "RVOL: " + str(row.get("rel_volume", "n/a"))
            + " · Vol: " + format_raw_count(row.get("volume", "n/a"))
            + " · Avg Vol: " + format_avg_vol(row.get("avg_volume", "n/a")) + "\n"
            "Float: " + format_millions(row.get("float", "n/a"))
            + " · Mkt Cap: " + format_millions(row.get("market_cap", "n/a"))
        )
        if send_telegram(msg):
            seen.add(key)
            sent += 1
    log.info("Dashboard scan done: %d matches, %d new alerts", len(rows), sent)
    save_seen(seen, DASHBOARD_SEEN_FILE)


def run_monitor():
    while True:
        scan_once()
        time.sleep(MONITOR_INTERVAL_SEC)


def test_telegram_connectivity():
    start = time.time()
    ok = send_telegram("🔧 Connectivity test from GitHub Actions runner (" + current_et().strftime("%Y-%m-%d %H:%M:%S ET") + ")")
    log.info("Telegram connectivity test: %s (%.1fs)", "OK" if ok else "FAILED", time.time() - start)


def test_fake_alerts():
    # Fake data only — no Finviz call, no seen-cache writes. Pure format preview.
    fake_rows = [
        {"ticker": "TESTA", "price": "4.20", "change": "+32.5%", "rel_volume": "5.1",
         "volume": "2450000", "avg_volume": "480.00", "float": "8.50", "market_cap": "42.00"},
        {"ticker": "TESTB", "price": "12.75", "change": "+18.9%", "rel_volume": "3.8",
         "volume": "1875000", "avg_volume": "620.00", "float": "15.20", "market_cap": "310.50"},
    ]
    sent = 0
    for row in fake_rows:
        msg = (
            "🧪 <b>TEST ALERT — fake data, not a real signal</b>\n"
            "$" + row["ticker"] + " | $" + row["price"] + " (" + row["change"] + ")\n"
            "RVOL: " + row["rel_volume"]
            + " · Vol: " + format_raw_count(row["volume"])
            + " · Avg Vol: " + format_avg_vol(row["avg_volume"]) + "\n"
            "Float: " + format_millions(row["float"])
            + " · Mkt Cap: " + format_millions(row["market_cap"])
        )
        if send_telegram(msg):
            sent += 1
    log.info("Fake alert test done: %d/%d sent", sent, len(fake_rows))


# ---------------- ENTRY ----------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "brief"
    if mode == "monitor":
        run_monitor()
    elif mode == "scan":
        scan_once()
    elif mode == "finviz":
        scan_finviz()
    elif mode == "dashboard":
        run_dashboard()
    elif mode == "test-telegram":
        test_telegram_connectivity()
    elif mode == "test-alerts":
        test_fake_alerts()
    else:
        run_brief()
