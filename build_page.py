#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_page.py
=============

קורא את קובץ התוצאות של graham_screener.py ובונה ממנו דף אינטרנט סטטי
(docs/index.html) שאפשר לפרסם ב-GitHub Pages.

שימוש:
    python3 build_page.py --in results.csv --out docs/index.html
"""

import argparse
import html
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

CRITERIA = [
    ("crit_1_adequate_size", "גודל מספיק", "מכירות שנתיות מעל סף מינימלי (חברה גדולה ויציבה)"),
    ("crit_2_strong_financial_condition", "מצב פיננסי איתן", "יחס שוטף ≥ 2, וחוב שאינו עולה על הנכסים השוטפים נטו"),
    ("crit_3_earnings_stability", "יציבות רווחים", "רווח חיובי בכל אחת מהשנים שנבדקו"),
    ("crit_4_dividend_record_20y", "היסטוריית דיבידנד", "תשלום דיבידנד רצוף של 20 שנה לפחות"),
    ("crit_5_earnings_growth_33pct", "צמיחת רווחים", "גידול של לפחות שליש ברווח למניה על פני התקופה"),
    ("crit_6_moderate_pe_15", "מכפיל רווח סביר", "מחיר/רווח ≤ 15"),
    ("crit_7_moderate_pb_or_pe_x_pb", "מכפיל הון סביר", "מחיר/הון ≤ 1.5, או מכפיל רווח × מכפיל הון ≤ 22.5"),
]

ISRAEL_TZ = timezone(timedelta(hours=3))


def fmt(value, digits=2):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def fmt_int(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "—"


def build_rows(df):
    rows = []
    for _, r in df.iterrows():
        checks = []
        for key, _label, _desc in CRITERIA:
            val = r.get(key)
            if pd.isna(val):
                checks.append(None)
            else:
                checks.append(bool(val) if not isinstance(val, str) else val.strip().lower() == "true")
        rows.append(
            {
                "ticker": str(r.get("ticker", "")),
                "name": str(r.get("name", "") or ""),
                "sector": str(r.get("sector", "") or ""),
                "score": int(r.get("score", 0) or 0),
                "max_score": int(r.get("max_score", 7) or 7),
                "pe": None if pd.isna(r.get("P/E")) else round(float(r.get("P/E")), 2),
                "pb": None if pd.isna(r.get("P/B")) else round(float(r.get("P/B")), 2),
                "cr": None if pd.isna(r.get("current_ratio")) else round(float(r.get("current_ratio")), 2),
                "div": None if pd.isna(r.get("dividend_years_streak")) else int(r.get("dividend_years_streak")),
                "rev": None if pd.isna(r.get("revenue")) else float(r.get("revenue")),
                "checks": checks,
            }
        )
    return rows


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>סורק גראהם — S&amp;P 500</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@500;700&family=IBM+Plex+Sans+Hebrew:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f6f4ef; --paper:#fffdf9; --ink:#1c1a17; --muted:#6c6659; --line:#e2ddd1;
  --accent:#8a6a3b; --good-bg:#e4efe2; --good-ink:#2f6b34; --bad-bg:#f3e6e4; --bad-ink:#94413a;
  --unknown-bg:#eceae4; --unknown-ink:#8c867a; --shadow:0 1px 3px rgba(28,26,23,.07);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#161513; --paper:#1f1e1b; --ink:#eae6dd; --muted:#9a9488; --line:#333029;
    --accent:#c9a465; --good-bg:#1f3322; --good-ink:#8fcf93; --bad-bg:#3a2321; --bad-ink:#e29b93;
    --unknown-bg:#2a2824; --unknown-ink:#8b857a; --shadow:0 1px 3px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"]{
  --bg:#161513; --paper:#1f1e1b; --ink:#eae6dd; --muted:#9a9488; --line:#333029;
  --accent:#c9a465; --good-bg:#1f3322; --good-ink:#8fcf93; --bad-bg:#3a2321; --bad-ink:#e29b93;
  --unknown-bg:#2a2824; --unknown-ink:#8b857a; --shadow:0 1px 3px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink); direction:rtl; text-align:right;
  font-family:"IBM Plex Sans Hebrew",system-ui,sans-serif; font-size:15px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px; margin:0 auto; padding:32px 16px 72px}
header{margin-bottom:28px}
.eyebrow{font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:var(--accent); font-weight:600}
h1{font-family:"Frank Ruhl Libre",Georgia,serif; font-size:clamp(28px,4.4vw,40px); margin:6px 0 8px; font-weight:700}
.sub{color:var(--muted); max-width:62ch; margin:0}
.updated{margin-top:14px; font-size:13px; color:var(--muted)}
.updated b{color:var(--ink); font-weight:600; font-family:"IBM Plex Mono",monospace; direction:ltr; unicode-bidi:isolate; display:inline-block}
.stats{display:flex; flex-wrap:wrap; gap:10px; margin:22px 0 8px}
.stat{background:var(--paper); border:1px solid var(--line); border-radius:12px; padding:10px 16px; box-shadow:var(--shadow)}
.stat .n{font-family:"IBM Plex Mono",monospace; font-size:21px; font-weight:500; unicode-bidi:isolate; display:block}
.stat .l{font-size:12px; color:var(--muted)}
.panel{background:var(--paper); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow); margin:18px 0; overflow:hidden}
.panel > summary{cursor:pointer; padding:14px 18px; font-weight:600; list-style:none}
.panel > summary::-webkit-details-marker{display:none}
.panel > summary::before{content:"▸"; margin-inline-end:8px; color:var(--accent); display:inline-block; transition:transform .15s}
.panel[open] > summary::before{transform:rotate(-90deg)}
.panel .body{padding:2px 18px 18px; color:var(--muted)}
.panel .body ol{padding-inline-start:20px; margin:8px 0}
.panel .body li{margin:7px 0}
.panel .body b{color:var(--ink)}
.toolbar{display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:20px 0 12px}
input[type=search],select{
  font:inherit; color:var(--ink); background:var(--paper); border:1px solid var(--line);
  border-radius:10px; padding:9px 13px; min-width:190px;
}
input[type=search]:focus,select:focus{outline:2px solid var(--accent); outline-offset:1px}
.tablewrap{background:var(--paper); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow); overflow-x:auto}
table{border-collapse:collapse; width:100%; min-width:900px}
th,td{padding:10px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap}
th{position:sticky; top:0; background:var(--paper); font-size:12px; color:var(--muted); font-weight:600; cursor:pointer; user-select:none; z-index:1}
th:hover{color:var(--accent)}
th .arrow{font-size:9px; opacity:.55}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 6%,transparent)}
td.name,td.sector{white-space:normal; direction:ltr; unicode-bidi:isolate; text-align:right}
td.name{min-width:200px}
td.tk{font-family:"IBM Plex Mono",monospace; font-weight:500; direction:ltr; unicode-bidi:isolate; text-align:right}
td.num{font-family:"IBM Plex Mono",monospace; direction:ltr; unicode-bidi:isolate; text-align:left; font-variant-numeric:tabular-nums}
.score{display:inline-flex; align-items:center; gap:5px; padding:3px 10px; border-radius:999px; font-weight:600;
  font-family:"IBM Plex Mono",monospace; font-size:13px; direction:ltr; unicode-bidi:isolate}
.s-hi{background:var(--good-bg); color:var(--good-ink)}
.s-mid{background:var(--unknown-bg); color:var(--unknown-ink)}
.s-lo{background:var(--bad-bg); color:var(--bad-ink)}
.dots{display:inline-flex; gap:4px; direction:ltr}
.dot{width:11px; height:11px; border-radius:50%; display:inline-block}
.dot.y{background:var(--good-ink)} .dot.n{background:var(--bad-ink); opacity:.45} .dot.u{background:var(--unknown-ink); opacity:.3}
footer{margin-top:34px; font-size:12.5px; color:var(--muted); line-height:1.75}
footer a{color:var(--accent)}
.empty{padding:36px; text-align:center; color:var(--muted)}
@media (max-width:640px){ .wrap{padding:22px 16px 56px} }
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">בנג'מין גראהם · המשקיע הנבון, פרק 14</div>
  <h1>סורק גראהם — S&amp;P 500</h1>
  <p class="sub">כל מניות מדד ה-S&amp;P 500 נבדקות מדי יום מסחר מול שבעת הקריטריונים של גראהם למשקיע המגן. הציון מציין כמה קריטריונים המניה עברה מתוך שבעה.</p>
  <div class="updated">עודכן לאחרונה: <b>__UPDATED__</b> · נסרקו <b>__COUNT__</b> מניות</div>
</header>

<div class="stats">__STATS__</div>

<details class="panel">
  <summary>מהם שבעת הקריטריונים?</summary>
  <div class="body">
    <ol>__CRITERIA_LIST__</ol>
    <p style="margin-top:14px">הערה חשובה: הספרים של גראהם נכתבו לפני עשרות שנים, וסף "הגודל המספיק" המקורי (100 מיליון דולר מכירות ב-1970) עודכן כאן להתאמה לאינפלציה. בנוסף, מקור הנתונים החינמי (Yahoo Finance) מספק בדרך כלל 4-5 שנות דוחות ולא 10, כך שקריטריוני היציבות והצמיחה נבדקים על התקופה הזמינה.</p>
  </div>
</details>

<div class="toolbar">
  <input type="search" id="q" placeholder="חיפוש לפי טיקר, שם או סקטור…" aria-label="חיפוש">
  <select id="minScore" aria-label="ציון מינימלי">
    <option value="0">כל הציונים</option>
    <option value="7">7 מתוך 7 בלבד</option>
    <option value="6">6 ומעלה</option>
    <option value="5">5 ומעלה</option>
    <option value="4">4 ומעלה</option>
  </select>
</div>

<div class="tablewrap">
  <table id="tbl">
    <thead><tr>
      <th data-k="ticker">טיקר <span class="arrow"></span></th>
      <th data-k="name">שם <span class="arrow"></span></th>
      <th data-k="sector">סקטור <span class="arrow"></span></th>
      <th data-k="score">ציון <span class="arrow">▼</span></th>
      <th data-k="checks">קריטריונים 1–7 <span class="arrow"></span></th>
      <th data-k="pe">מכפיל רווח <span class="arrow"></span></th>
      <th data-k="pb">מכפיל הון <span class="arrow"></span></th>
      <th data-k="cr">יחס שוטף <span class="arrow"></span></th>
      <th data-k="div">שנות דיבידנד <span class="arrow"></span></th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table>
  <div class="empty" id="noRows" hidden>לא נמצאו מניות התואמות את הסינון.</div>
</div>

<footer>
  <p><b>זה אינו ייעוץ השקעות.</b> הדף מציג סינון טכני-כמותי בלבד, המבוסס על נתונים אוטומטיים מ-Yahoo Finance שעשויים להיות חלקיים או שגויים. ציון גבוה אינו המלצה לקנות, וציון נמוך אינו המלצה למכור. יש לבדוק כל מניה לעומק ולהתייעץ עם בעל רישיון לפני כל החלטת השקעה.</p>
  <p>נבנה אוטומטית מדי יום מסחר · נתונים: Yahoo Finance דרך yfinance · קריטריונים: "המשקיע הנבון", פרק 14</p>
</footer>
</div>

<script>
const DATA = __DATA__;
const CRIT_LABELS = __CRIT_LABELS__;
let sortKey = "score", sortDir = -1;

const tb = document.getElementById("tb");
const q = document.getElementById("q");
const minScore = document.getElementById("minScore");
const noRows = document.getElementById("noRows");

function scoreClass(s, m){ const r = s / m; return r >= 0.85 ? "s-hi" : (r >= 0.55 ? "s-mid" : "s-lo"); }
function num(v){ return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US",{minimumFractionDigits:2, maximumFractionDigits:2}); }
function intv(v){ return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US"); }

function dots(checks){
  return '<span class="dots">' + checks.map((c,i) => {
    const cls = c === null ? "u" : (c ? "y" : "n");
    const state = c === null ? "אין נתון" : (c ? "עבר" : "לא עבר");
    return `<span class="dot ${cls}" title="${CRIT_LABELS[i]}: ${state}"></span>`;
  }).join("") + "</span>";
}

function render(){
  const term = q.value.trim().toLowerCase();
  const min = Number(minScore.value);
  let rows = DATA.filter(r => r.score >= min);
  if (term) rows = rows.filter(r =>
    r.ticker.toLowerCase().includes(term) ||
    r.name.toLowerCase().includes(term) ||
    r.sector.toLowerCase().includes(term));

  rows.sort((a,b) => {
    let x = a[sortKey], y = b[sortKey];
    if (sortKey === "checks"){ x = a.score; y = b.score; }
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return sortDir * x.localeCompare(y);
    return sortDir * (x - y);
  });

  noRows.hidden = rows.length > 0;
  tb.innerHTML = rows.map(r => `<tr>
    <td class="tk">${r.ticker}</td>
    <td class="name">${r.name}</td>
    <td class="sector">${r.sector || "—"}</td>
    <td><span class="score ${scoreClass(r.score, r.max_score)}">${r.score} / ${r.max_score}</span></td>
    <td>${dots(r.checks)}</td>
    <td class="num">${num(r.pe)}</td>
    <td class="num">${num(r.pb)}</td>
    <td class="num">${num(r.cr)}</td>
    <td class="num">${intv(r.div)}</td>
  </tr>`).join("");
}

document.querySelectorAll("th[data-k]").forEach(th => {
  th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (k === sortKey) sortDir = -sortDir;
    else { sortKey = k; sortDir = (k === "ticker" || k === "name" || k === "sector") ? 1 : -1; }
    document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
    th.querySelector(".arrow").textContent = sortDir === 1 ? "▲" : "▼";
    render();
  });
});
q.addEventListener("input", render);
minScore.addEventListener("change", render);
render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="בונה דף אינטרנט מתוצאות סורק גראהם")
    ap.add_argument("--in", dest="inp", default="results.csv")
    ap.add_argument("--out", dest="out", default="docs/index.html")
    args = ap.parse_args()

    df = pd.read_csv(args.inp)
    if "error" in df.columns:
        df = df[df["error"].isna()]
    df = df.sort_values(["score", "ticker"], ascending=[False, True])

    rows = build_rows(df)
    total = len(rows)
    max_score = rows[0]["max_score"] if rows else 7

    buckets = [(7, "עברו 7 מתוך 7"), (6, "עברו 6 ומעלה"), (5, "עברו 5 ומעלה")]
    stats_html = f'<div class="stat"><span class="n">{total}</span><span class="l">מניות נסרקו</span></div>'
    for threshold, label in buckets:
        if threshold > max_score:
            continue
        n = sum(1 for r in rows if r["score"] >= threshold)
        stats_html += f'<div class="stat"><span class="n">{n}</span><span class="l">{label}</span></div>'

    criteria_html = "".join(
        f"<li><b>{html.escape(label)}</b> — {html.escape(desc)}</li>" for _key, label, desc in CRITERIA
    )

    updated = datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")

    page = (
        PAGE_TEMPLATE.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
        .replace("__CRIT_LABELS__", json.dumps([c[1] for c in CRITERIA], ensure_ascii=False))
        .replace("__UPDATED__", updated)
        .replace("__COUNT__", str(total))
        .replace("__STATS__", stats_html)
        .replace("__CRITERIA_LIST__", criteria_html)
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[done] נכתב דף עם {total} מניות אל {args.out}")


if __name__ == "__main__":
    main()
