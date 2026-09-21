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
    ("crit_1_adequate_size", "גודל מספיק", "מכירות שנתיות מעל סף מינימלי (בחברת תשתית או פיננסית נבדק סך הנכסים במקום המכירות)"),
    ("crit_2_strong_financial_condition", "מצב פיננסי איתן", "יחס שוטף של 2 לפחות, וחוב שאינו עולה על הנכסים השוטפים נטו (בחברת תשתית: חוב של עד פי 2 מההון העצמי; בחברה פיננסית המבחן אינו חל)"),
    ("crit_3_earnings_stability", "יציבות רווחים", "רווח חיובי בכל אחת מהשנים שנבדקו"),
    ("crit_4_dividend_record_20y", "היסטוריית דיבידנד", "תשלום דיבידנד רצוף של 20 שנה לפחות"),
    ("crit_5_earnings_growth_33pct", "צמיחת רווחים", "גידול של לפחות שליש ברווח למניה על פני התקופה"),
    ("crit_6_moderate_pe_15", "מכפיל רווח סביר", "מחיר חלקי רווח, עד 15"),
    ("crit_7_moderate_pb_or_pe_x_pb", "מכפיל הון סביר", "מחיר חלקי הון, עד 1.5 - או לחלופין מכפיל רווח כפול מכפיל הון, עד 22.5"),
]

FSCORE_LABELS = [
    ("f_1_roa_positive", "רווח חיובי"),
    ("f_2_cfo_positive", "תזרים תפעולי חיובי"),
    ("f_3_roa_improving", "תשואה על הנכסים משתפרת"),
    ("f_4_cfo_above_income", "תזרים גבוה מהרווח החשבונאי"),
    ("f_5_leverage_down", "המינוף לא גדל"),
    ("f_6_current_ratio_up", "הנזילות משתפרת"),
    ("f_7_no_new_shares", "לא הונפקו מניות חדשות"),
    ("f_8_gross_margin_up", "שולי הרווח הגולמי משתפרים"),
    ("f_9_asset_turnover_up", "יעילות השימוש בנכסים משתפרת"),
]

TYPE_LABELS = {
    "industrial": "תעשייתית",
    "utility": "תשתית",
    "financial": "פיננסית",
}

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


def _tri(val):
    """מתרגם ערך מהטבלה ל-True / False / None (None = אין נתון או לא רלוונטי)."""
    if val is None or (not isinstance(val, str) and pd.isna(val)):
        return None
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1"):
            return True
        if v in ("false", "0"):
            return False
        return None
    return bool(val)


def build_rows(df):
    rows = []
    for _, r in df.iterrows():
        checks = [_tri(r.get(key)) for key, _label, _desc in CRITERIA]
        fchecks = [_tri(r.get(key)) for key, _label in FSCORE_LABELS]
        ctype = str(r.get("company_type") or "industrial")
        rows.append(
            {
                "ctype": ctype,
                "ctype_label": TYPE_LABELS.get(ctype, ctype),
                "fscore": None if pd.isna(r.get("fscore")) else int(r.get("fscore")),
                "fchecks": fchecks,
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
  <p class="sub">כל מניות מדד ה-S&amp;P 500 נבדקות מדי יום מסחר מול שבעת הקריטריונים של גראהם למשקיע המגן, בהתאמה לסוג החברה, ולצדם ציון פיוטרוסקי (F-Score) שבודק אם המצב הפיננסי משתפר או מידרדר.</p>
  <div class="updated">עודכן לאחרונה: <b>__UPDATED__</b> · נסרקו <b>__COUNT__</b> מניות</div>
</header>

<div class="stats">__STATS__</div>

<details class="panel">
  <summary>מהם שבעת הקריטריונים?</summary>
  <div class="body">
    <ol>__CRITERIA_LIST__</ol>
    <p style="margin-top:14px"><b>התאמה לסוג החברה.</b> גראהם ניסח את הקריטריונים האלה עבור חברות תעשייתיות, עם כללים מותאמים לחברות תשתית. למאזן של בנק, חברת ביטוח או קרן ריט אין חלוקה משמעותית בין נכסים שוטפים להתחייבויות שוטפות, ולכן מבחן היחס השוטף פשוט לא חל עליהן. בדף הזה הוא מסומן אצלן כלא-רלוונטי (נקודה אפורה) ויוצא מהמכנה, כך שחברה פיננסית מדורגת מתוך 6 ולא מתוך 7. לחברות תשתית, במקום היחס השוטף, נבדק יחס חוב להון עצמי של עד 2.</p>
    <p>הספרים של גראהם נכתבו לפני עשרות שנים, וסף "הגודל המספיק" המקורי (100 מיליון דולר מכירות ב-1970) עודכן כאן להתאמה לאינפלציה. בנוסף, מקור הנתונים החינמי (Yahoo Finance) מספק בדרך כלל 4-5 שנות דוחות ולא 10, כך שקריטריוני היציבות והצמיחה נבדקים על התקופה הזמינה.</p>
  </div>
</details>

<details class="panel">
  <summary>מהו ציון F-Score?</summary>
  <div class="body">
    <p>ציון פיוטרוסקי הוא בדיקה משלימה בת תשע נקודות, שנועדה לענות על שאלה שהקריטריונים של גראהם לא שואלים: האם מצבה הפיננסי של החברה <b>משתפר או מידרדר</b> בשנה האחרונה. שיטת גראהם מצלמת תמונת מצב סטטית; פיוטרוסקי מוסיף את הכיוון.</p>
    <p>הרעיון המקורי היה להפריד, בתוך רשימת מניות זולות, בין חברות זולות-ומשתפרות לבין חברות זולות-ומתדרדרות (מלכודות ערך). ציון 8-9 נחשב חזק, 0-2 חלש.</p>
    <ol>__FSCORE_LIST__</ol>
    <p>ריחוף מעל הציון בטבלה מציג אילו מהמבחנים עברו.</p>
  </div>
</details>

<div class="toolbar">
  <input type="search" id="q" placeholder="חיפוש לפי טיקר, שם או סקטור…" aria-label="חיפוש">
  <select id="minScore" aria-label="ציון גראהם מינימלי">
    <option value="0">כל ציוני גראהם</option>
    <option value="1.0">עברו את כל הקריטריונים הרלוונטיים</option>
    <option value="0.85">85% ומעלה מהקריטריונים</option>
    <option value="0.7">70% ומעלה מהקריטריונים</option>
  </select>
  <select id="minF" aria-label="F-Score מינימלי">
    <option value="0">כל ציוני F-Score</option>
    <option value="8">F-Score 8 ומעלה</option>
    <option value="7">F-Score 7 ומעלה</option>
    <option value="6">F-Score 6 ומעלה</option>
  </select>
  <select id="ctype" aria-label="סוג חברה">
    <option value="">כל סוגי החברות</option>
    <option value="industrial">תעשייתיות</option>
    <option value="financial">פיננסיות</option>
    <option value="utility">תשתית</option>
  </select>
</div>

<div class="tablewrap">
  <table id="tbl">
    <thead><tr>
      <th data-k="ticker">טיקר <span class="arrow"></span></th>
      <th data-k="name">שם <span class="arrow"></span></th>
      <th data-k="sector">סקטור <span class="arrow"></span></th>
      <th data-k="score">ציון גראהם <span class="arrow">▼</span></th>
      <th data-k="checks">קריטריונים <span class="arrow"></span></th>
      <th data-k="fscore">F-Score <span class="arrow"></span></th>
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
const F_LABELS = __F_LABELS__;
let sortKey = "score", sortDir = -1;

const tb = document.getElementById("tb");
const q = document.getElementById("q");
const minScore = document.getElementById("minScore");
const minF = document.getElementById("minF");
const ctypeSel = document.getElementById("ctype");
const noRows = document.getElementById("noRows");

function scoreClass(s, m){ const r = s / m; return r >= 0.85 ? "s-hi" : (r >= 0.55 ? "s-mid" : "s-lo"); }
function num(v){ return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US",{minimumFractionDigits:2, maximumFractionDigits:2}); }
function intv(v){ return v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US"); }

function dots(checks){
  return '<span class="dots">' + checks.map((c,i) => {
    const cls = c === null ? "u" : (c ? "y" : "n");
    const state = c === null ? "לא רלוונטי לסוג החברה" : (c ? "עבר" : "לא עבר");
    return `<span class="dot ${cls}" title="${CRIT_LABELS[i]}: ${state}"></span>`;
  }).join("") + "</span>";
}

function fTitle(r){
  if (r.fscore === null) return "אין מספיק נתונים לחישוב";
  return r.fchecks.map((c,i) => `${c ? "✓" : "✗"} ${F_LABELS[i]}`).join(" · ");
}

function render(){
  const term = q.value.trim().toLowerCase();
  const minRatio = Number(minScore.value);
  const minFv = Number(minF.value);
  const wantType = ctypeSel.value;
  let rows = DATA.filter(r => (r.max_score ? r.score / r.max_score : 0) >= minRatio);
  if (minFv) rows = rows.filter(r => r.fscore !== null && r.fscore >= minFv);
  if (wantType) rows = rows.filter(r => r.ctype === wantType);
  if (term) rows = rows.filter(r =>
    r.ticker.toLowerCase().includes(term) ||
    r.name.toLowerCase().includes(term) ||
    r.sector.toLowerCase().includes(term));

  rows.sort((a,b) => {
    let x = a[sortKey], y = b[sortKey];
    // ציון גראהם ממוין לפי שיעור הקריטריונים שעברו, כי המכנה משתנה לפי סוג החברה
    if (sortKey === "score" || sortKey === "checks"){
      x = a.max_score ? a.score / a.max_score : 0;
      y = b.max_score ? b.score / b.max_score : 0;
      if (x === y){ x = a.score; y = b.score; }
    }
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
    <td><span class="score ${scoreClass(r.score, r.max_score)}" title="${r.ctype_label} — ${r.max_score} קריטריונים רלוונטיים">${r.score} / ${r.max_score}</span></td>
    <td>${dots(r.checks)}</td>
    <td><span class="score ${r.fscore === null ? 's-mid' : scoreClass(r.fscore, 9)}" title="${fTitle(r)}">${r.fscore === null ? "—" : r.fscore + " / 9"}</span></td>
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
[minScore, minF, ctypeSel].forEach(el => el.addEventListener("change", render));
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

    def ratio(r):
        return (r["score"] / r["max_score"]) if r["max_score"] else 0

    n_perfect = sum(1 for r in rows if ratio(r) >= 1.0)
    n_strong = sum(1 for r in rows if ratio(r) >= 0.85)
    n_both = sum(1 for r in rows if ratio(r) >= 1.0 and (r["fscore"] or 0) >= 7)

    stats = [
        (total, "מניות נסרקו"),
        (n_perfect, "עברו את כל הקריטריונים"),
        (n_strong, "85% ומעלה מהקריטריונים"),
        (n_both, "גם כל הקריטריונים וגם F-Score 7+"),
    ]
    stats_html = "".join(
        f'<div class="stat"><span class="n">{n}</span><span class="l">{html.escape(label)}</span></div>'
        for n, label in stats
    )

    criteria_html = "".join(
        f"<li><b>{html.escape(label)}</b> — {html.escape(desc)}</li>" for _key, label, desc in CRITERIA
    )
    fscore_html = "".join(f"<li>{html.escape(label)}</li>" for _key, label in FSCORE_LABELS)

    updated = datetime.now(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")

    page = (
        PAGE_TEMPLATE.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
        .replace("__CRIT_LABELS__", json.dumps([c[1] for c in CRITERIA], ensure_ascii=False))
        .replace("__F_LABELS__", json.dumps([f[1] for f in FSCORE_LABELS], ensure_ascii=False))
        .replace("__FSCORE_LIST__", fscore_html)
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
