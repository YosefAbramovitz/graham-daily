#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""בונה את דף השלב הטכני מתוך tech_results.csv."""

import argparse
import datetime as dt
import html
import json
import os

import pandas as pd

SIGNAL_CLASS = {
    "אזור כניסה": "good",
    "היפוך חיובי טרי": "good",
    "מעבר משטר": "good",
    "בתמיכה, ממתין למומנטום": "warm",
    "מגמה תקינה": "neutral",
    "מתוח": "warm",
    "איתות יציאה": "bad",
    "שבר את תעלת השורי": "bad",
    "מעבר, לא ברור": "neutral",
    "ריבאונד בשוק דובי": "bad",
    "מגמה שלילית": "bad",
}
DISQUALIFIED_PREFIX = "נפסל במבחן"   # התווית נבנית דינמית עם שם המבחן שנכשל

BOOK_NOTES = [
    ("כללי הטווח של ה-RSI (פרק 1)",
     "האמונה הרווחת ש-RSI מתחת ל-30 הוא מכירת יתר ומעל 70 קניית יתר נכונה רק בשוק "
     "חסר מגמה. בראון מראה שהתעלה שבה ה-RSI נע נקבעת לפי המגמה: בשוק שורי הוא נע בין "
     "תמיכה של 40 עד 50 להתנגדות של 80 עד 90, ובשוק דובי בין תמיכה של 20 עד 30 "
     "להתנגדות של 55 עד 65. לכן העמודה כאן מציגה את מיקום ה-RSI בתוך התעלה של המניה "
     "עצמה, ולא מול מספר קבוע."),
    ("איך נקבע המשטר",
     "בראון לא נותנת מבחן מכני, ולכן המשטר כאן נקבע בהצלבה של שני מבחנים: המיקום מול "
     "ממוצע נע של 200 יום והכיוון שלו, והתנהגות ה-RSI עצמו לאורך 120 הנרות האחרונים. "
     "כששני המבחנים מסכימים הביטחון מסומן כוודאי. כשהם חלוקים המניה מסומנת כמצב מעבר, "
     "וזה בדיוק השלב שבראון מתארת בין המשטרים."),
    ("Positive Reversal ויעד המחיר (פרק 8)",
     "תבנית שורית שבה ה-RSI עושה שפל נמוך יותר בזמן שהמחיר עושה שפל גבוה יותר. "
     "יעד המחיר מחושב בנוסחה מנספח D של הספר. בדיקת הנוסחה מול הדוגמה שבספר עצמו, על "
     "ה-S&P 500, מחזירה בדיוק את היעד שבראון מציגה שם."),
    ("האוסילטור הנגזר (פרק 14)",
     "RSI מוחלק שלוש פעמים: ממוצע אקספוננציאלי של 5, עליו ממוצע אקספוננציאלי של 3, "
     "ומההפרש מול ממוצע פשוט של 9 מתקבלת היסטוגרמה. כיוון עולה עם ערך שלילי הוא "
     "התאוששות מוקדמת, כיוון יורד עם ערך חיובי הוא היחלשות. בראון מזהירה שהנוסחה "
     "מיועדת למניות ולמדדי מניות ואין להשתמש בה על אג\"ח."),
    ("שלב האיכות שקודם לדף הזה",
     "לפני החישוב הטכני עוברות המניות סינון נוסף, לפי הצנרת של Wesley Gray ו-Tobias "
     "Carlisle בספר Quantitative Value: קודם פוסלים ורק אחר כך מדרגים. מדד בניש "
     "מזהה סימנים של מניפולציה בדוחות, ומדד אלטמן מזהה סכנת חדלות פירעון. מניה "
     "שנכשלת באחד מהם מסומנת כנפסלת ואינה יכולה לקבל איתות כניסה, גם אם התמונה "
     "הטכנית שלה מושלמת. מדד אלטמן אינו מחושב לבנקים ולחברות ביטוח מפני שהוא "
     "פותח לחברות תעשייה ואינו תקף למבנה המאזן שלהן."),
    ("ציון האיכות",
     "ממוצע של ארבעה דירוגים אחוזוניים בתוך הרשימה של היום: EBIT חלקי שווי פעילות "
     "(מדד הזול שנמצא החזק ביותר אצל גריי וקרלייל), רווחיות גולמית חלקי סך הנכסים "
     "(המדד של נובי-מרקס), מומנטום של שנים עשר חודשים בהשמטת החודש האחרון, "
     "ותשואת החזר הון נטו. זהו דירוג יחסי ולא ציון מוחלט — 90 פירושו שהמניה בעשירון "
     "העליון של הרשימה הנוכחית, לא שהיא טובה במונחים מוחלטים."),
    ("כיוון: למה הכל לונג",
     "סינון גראהם מאתר חברות זולות ויציבות פיננסית, ומרשימה כזו אי אפשר לגזור "
     "מועמדות לשורט — שורט דורש את ההפך, חברה יקרה ומתדרדרת. לכן כל איתות כניסה "
     "כאן הוא לונג. איתותי היציאה מיועדים למי שכבר מחזיק במניה, ולא לפתיחת "
     "פוזיציה הפוכה."),
    ("אופק הזמן: מאיפה המספר",
     "לכל מניה נסרקות שש שנות מסחר אחורה, מאותרות בה כל תבניות ה-Positive Reversal "
     "הקודמות, ונבדק לגבי כל אחת אם המחיר אכן הגיע ליעד שהנוסחה חישבה ותוך כמה "
     "ימי מסחר. ההנחה היא שהעסקה נפתחת רק חמישה נרות אחרי השפל, כי רק אז אפשר "
     "לדעת שהיה שם שפל. המספר המוצג הוא החציון של המקרים שהגיעו ליעד, לצד כמה "
     "מתוך כמה הגיעו. זו סטטיסטיקה לאחור על המניה עצמה, לא תחזית, ומדגם קטן "
     "מדי מסומן ככזה."),
    ("האופק הערכי נפרד לגמרי",
     "מרווח הביטחון של גראהם, המוצג בעמודה הימנית, נסגר בדרך כלל על פני שנה עד "
     "שלוש ולא על פני שבועות. אופק הזמן הטכני עונה על השאלה מתי להיכנס ומתי "
     "התבנית מיצתה את עצמה, לא על השאלה כמה זמן להחזיק את ההשקעה."),
    ("סטופ, יחס סיכון וגודל פוזיציה",
     "הסטופ אינו אחוז שרירותי אלא הרמה שבה התבנית מתבטלת: השפל הנמוך ביותר מאז "
     "הציר שיצר את האיתות, פחות חצי ATR כמרווח רעש. יחס הסיכון לתשואה הוא המרחק "
     "ליעד חלקי המרחק לסטופ, ומתחת ל-1 האיתות אינו משתלם גם אם הוא נכון. גודל "
     "הפוזיציה מחושב בדפדפן שלך מהמספרים שתזין ואינו נשמר בשום מקום. סטופ צמוד "
     "מייצר כמות עצומה, ולכן יש תקרת ריכוזיות של 20 אחוז מהתיק למניה בודדת, "
     "והדף מסמן מתי היא נכנסה לפעולה."),
    ("אזהרה שחוזרת לאורך כל הספר",
     "בראון חוזרת ומדגישה שאף אחד מהאיתותים האלה אינו איתות מסחר בפני עצמו. היא כותבת "
     "במפורש שלקנות או למכור על סמך דפוס אחד בלבד הוא מתכון להפסד, ושכל איתות חייב "
     "להיות מלווה ביעד מחיר ובגורם נוסף שמאשר אותו. הדף הזה הוא שכבת תזמון על גבי "
     "סינון ערכי, לא מערכת מסחר."),
]

PAGE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>השלב הטכני — סורק גראהם</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@500;700&family=IBM+Plex+Sans+Hebrew:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f6f4ef; --paper:#fffdf9; --ink:#1c1a17; --muted:#6c6659; --line:#e2ddd1;
  --accent:#8a6a3b; --good-bg:#e4efe2; --good-ink:#2f6b34; --bad-bg:#f3e6e4; --bad-ink:#94413a;
  --warm-bg:#f6eeda; --warm-ink:#8a6320;
  --unknown-bg:#eceae4; --unknown-ink:#8c867a; --shadow:0 1px 3px rgba(28,26,23,.07);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#161513; --paper:#1f1e1b; --ink:#eae6dd; --muted:#9a9488; --line:#333029;
    --accent:#c9a465; --good-bg:#1f3322; --good-ink:#8fcf93; --bad-bg:#3a2321; --bad-ink:#e29b93;
    --warm-bg:#352d1c; --warm-ink:#d9b56e;
    --unknown-bg:#2a2824; --unknown-ink:#8b857a; --shadow:0 1px 3px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"]{
  --bg:#161513; --paper:#1f1e1b; --ink:#eae6dd; --muted:#9a9488; --line:#333029;
  --accent:#c9a465; --good-bg:#1f3322; --good-ink:#8fcf93; --bad-bg:#3a2321; --bad-ink:#e29b93;
  --warm-bg:#352d1c; --warm-ink:#d9b56e;
  --unknown-bg:#2a2824; --unknown-ink:#8b857a; --shadow:0 1px 3px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink); direction:rtl; text-align:right;
  font-family:"IBM Plex Sans Hebrew",system-ui,sans-serif; font-size:15px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1560px; margin:0 auto; padding:32px 16px 72px}
header{margin-bottom:28px}
.eyebrow{font-size:12px; letter-spacing:.09em; text-transform:uppercase; color:var(--accent); font-weight:600}
h1{font-family:"Frank Ruhl Libre",Georgia,serif; font-size:clamp(28px,4.4vw,40px); margin:6px 0 8px; font-weight:700}
.sub{color:var(--muted); max-width:66ch; margin:0}
.updated{margin-top:14px; font-size:13px; color:var(--muted)}
.updated b{color:var(--ink); font-weight:600; font-family:"IBM Plex Mono",monospace; direction:ltr; unicode-bidi:isolate; display:inline-block}
.updated a{color:var(--accent); font-weight:600}
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
.panel .body h3{color:var(--ink); font-size:14.5px; margin:16px 0 4px}
.panel .body p{margin:4px 0}
.toolbar{display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:20px 0 12px}
input[type=search],select{
  font:inherit; color:var(--ink); background:var(--paper); border:1px solid var(--line);
  border-radius:10px; padding:9px 13px; min-width:190px;
}
input[type=search]:focus,select:focus,input[type=number]:focus{outline:2px solid var(--accent); outline-offset:1px}
.calc{gap:14px; align-items:center}
.calc .calclabel{font-weight:600}
.calc label{font-size:13px; color:var(--muted); display:inline-flex; align-items:center; gap:7px}
.calc input[type=number]{font:inherit; color:var(--ink); background:var(--paper); border:1px solid var(--line);
  border-radius:10px; padding:7px 11px; width:118px; direction:ltr; text-align:left;
  font-family:"IBM Plex Mono",monospace; font-size:13.5px}
.calc .calcnote{font-size:12px; color:var(--muted)}
.tablewrap{background:var(--paper); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow); overflow-x:auto}
table{border-collapse:collapse; width:100%; min-width:1620px}
th,td{padding:10px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; vertical-align:top}
th{position:sticky; top:0; background:var(--paper); font-size:12px; color:var(--muted); font-weight:600; cursor:pointer; user-select:none; z-index:1}
th:hover{color:var(--accent)}
th.nosort{cursor:default}
th.nosort:hover{color:var(--muted)}
th .arrow{font-size:9px; opacity:.55}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 6%,transparent)}
td.name{white-space:normal; direction:ltr; unicode-bidi:isolate; text-align:right; min-width:170px}
td.tk{font-family:"IBM Plex Mono",monospace; font-weight:500; direction:ltr; unicode-bidi:isolate; text-align:right}
td.num{font-family:"IBM Plex Mono",monospace; direction:ltr; unicode-bidi:isolate; text-align:left; font-variant-numeric:tabular-nums}
.tag{display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:12.5px}
.t-good{background:var(--good-bg); color:var(--good-ink)}
.t-bad{background:var(--bad-bg); color:var(--bad-ink)}
.t-warm{background:var(--warm-bg); color:var(--warm-ink)}
.t-neutral{background:var(--unknown-bg); color:var(--unknown-ink)}
.sm{display:block; font-size:11.5px; color:var(--muted); font-weight:400; margin-top:2px; white-space:nowrap}
.sm.ltr{direction:ltr; unicode-bidi:isolate; text-align:left; font-family:"IBM Plex Mono",monospace}
.bar{position:relative; width:118px; height:8px; border-radius:4px; background:var(--unknown-bg); margin-top:6px; direction:ltr}
.bar .zone{position:absolute; top:0; bottom:0; background:color-mix(in srgb,var(--accent) 26%,transparent)}
.bar .pin{position:absolute; top:-3px; width:3px; height:14px; border-radius:2px; background:var(--ink)}
.up{color:var(--good-ink); font-weight:600}
.down{color:var(--bad-ink); font-weight:600}
footer{margin-top:34px; font-size:12.5px; color:var(--muted); line-height:1.75}
footer a{color:var(--accent)}
.empty{padding:36px; text-align:center; color:var(--muted)}
@media (max-width:640px){ .wrap{padding:22px 16px 56px} }
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">שלב משלים · תזמון</div>
  <h1>השלב הטכני</h1>
  <p class="sub">שכבת תזמון על המניות שכבר עברו את הסינון הערכי של גראהם, לפי השיטות של
  קונסטנס בראון מתוך <i>Technical Analysis for the Trading Professional</i>. הסינון הערכי
  אומר מה שווה לקנות. הדף הזה אומר איפה המניה עומדת כרגע ביחס למגמה שלה.</p>
  <div class="updated">עודכן לאחרונה: <b>__UPDATED__</b> · <b>__COUNT__</b> מניות ·
    <a href="index.html">המשקיע המגן</a> · <a href="enterprising.html">המשקיע היוזם</a></div>
</header>

<div class="stats">__STATS__</div>

<details class="panel">
  <summary>מה מחושב כאן, ומה הספר אומר</summary>
  <div class="body">__NOTES__</div>
</details>

<div class="toolbar">
  <input type="search" id="q" placeholder="חיפוש לפי שם או סימול" autocomplete="off">
  <select id="kind">
    <option value="">הכל</option>
    <option value="כניסה">רק כניסות</option>
    <option value="החזקה">רק החזקות</option>
    <option value="יציאה">רק איתותי יציאה</option>
    <option value="פסילה">רק נפסלות</option>
  </select>
  <select id="sig"><option value="">כל האיתותים</option>__SIG_OPTS__</select>
  <select id="reg">
    <option value="">כל המשטרים</option>
    <option value="שורי">שורי</option>
    <option value="דובי">דובי</option>
    <option value="מעבר">מעבר</option>
  </select>
</div>

<div class="toolbar calc">
  <span class="calclabel">גודל פוזיציה:</span>
  <label>תיק בדולרים <input type="number" id="port" value="100000" min="0" step="1000"></label>
  <label>סיכון לעסקה באחוזים <input type="number" id="risk" value="1" min="0.1" max="10" step="0.1"></label>
  <span class="calcnote">מחושב בדפדפן שלך בלבד ואינו נשמר בשום שרת</span>
</div>

<div class="tablewrap">
  <table id="t">
    <thead><tr>
      <th data-k="signal_rank">איתות <span class="arrow">▲</span></th>
      <th data-k="horizon_weeks">כיוון ואופק <span class="arrow"></span></th>
      <th data-k="ticker">סימול <span class="arrow"></span></th>
      <th data-k="name">שם <span class="arrow"></span></th>
      <th data-k="price">מחיר <span class="arrow"></span></th>
      <th data-k="rsi">RSI ומיקום בתעלה <span class="arrow"></span></th>
      <th data-k="pos_rev_upside_pct">יעד <span class="arrow"></span></th>
      <th data-k="stop_pct">סטופ <span class="arrow"></span></th>
      <th data-k="risk_reward">סיכון / תשואה <span class="arrow"></span></th>
      <th class="nosort">כמות</th>
      <th data-k="deriv_osc">אוסילטור נגזר <span class="arrow"></span></th>
      <th data-k="dist_sma200_pct">מגמה <span class="arrow"></span></th>
      <th data-k="quality_score">איכות <span class="arrow"></span></th>
      <th data-k="margin_of_safety">גראהם <span class="arrow"></span></th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table>
  <div class="empty" id="none" hidden>אין מניות שתואמות את הסינון.</div>
</div>

<footer>
  <p>שלב האיכות מבוסס על Wesley R. Gray ו-Tobias E. Carlisle, <i>Quantitative Value</i>,
  Wiley 2012, ועל המאמרים המקוריים של Beneish (1999), Altman (1968) ו-Novy-Marx (2013).</p>
  <p>המקור לשיטות: Constance M. Brown, <i>Technical Analysis for the Trading Professional</i>,
  McGraw-Hill, 1999 — פרקים 1, 8 ו-14 ונספח D. מדד הקומפוזיט מפרק 12 אינו מחושב כאן מפני
  שהמחברת בחרה לא לפרסם את הנוסחה בספר.</p>
  <p>הנתונים מגיעים ממקור חינמי ועשויים להיות חלקיים או שגויים. זהו כלי סינון כמותי בלבד,
  אינו ייעוץ השקעות ואינו המלצה לקנות או למכור נייר ערך כלשהו.</p>
</footer>
</div>

<script>
const DATA = __DATA__;
const SIGCLASS = __SIGCLASS__;
let sortKey = "signal_rank", sortDir = 1;

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const num = v => (v === "" || v === null || v === undefined || isNaN(v)) ? null : Number(v);

function rsiCell(r){
  const v = num(r.rsi);
  if (v === null) return "<td>—</td>";
  const band = (r.regime === "שורי") ? [40,50,80,90] : [20,30,55,65];
  const zoneL = band[0], zoneR = band[3];
  const pctL = zoneL, pctW = zoneR - zoneL;
  const cls = r.rsi_zone.indexOf("תמיכה") >= 0 ? "t-good"
            : r.rsi_zone.indexOf("התנגדות") >= 0 ? "t-warm" : "t-neutral";
  const shift = r.regime_shift ? `<span class="sm">${esc(r.regime_shift)}</span>` : "";
  return `<td><span class="tag ${cls}">${v.toFixed(1)}</span>
    <span class="sm">${esc(r.regime)} · ${esc(r.rsi_zone)}</span>
    <div class="bar"><div class="zone" style="left:${pctL}%;width:${pctW}%"></div>
      <div class="pin" style="left:calc(${Math.max(0,Math.min(100,v))}% - 1.5px)"></div></div>
    <span class="sm ltr">${esc(r.band_support)} / ${esc(r.band_resistance)}</span>${shift}</td>`;
}

function derivCell(r){
  const v = num(r.deriv_osc);
  if (v === null) return "<td>—</td>";
  const up = r.deriv_dir === "עולה";
  return `<td class="num"><span class="${up?'up':'down'}">${up?"▲":"▼"} ${v.toFixed(2)}</span>
    <span class="sm ltr">${esc(r.deriv_bars_since_cross)} bars ${v>=0?"+":"−"}</span></td>`;
}

function targetCell(r){
  const t = num(r.pos_rev_target), up = num(r.pos_rev_upside_pct);
  if (t === null) return `<td class="num">—</td>`;
  const age = r.pos_rev_bars_ago === "" ? "" : `<span class="sm ltr">${r.pos_rev_bars_ago} bars ago</span>`;
  const cls = up > 0 ? "up" : "down";
  return `<td class="num"><span class="${cls}">${t.toFixed(2)}</span>
    <span class="sm ltr">${up>0?"+":""}${up.toFixed(1)}%</span>${age}</td>`;
}

const MAX_POSITION_PCT = 20;   // תקרת ריכוזיות למניה בודדת
const KINDCLASS = {"כניסה":"good","החזקה":"neutral","יציאה":"bad","המתנה":"neutral"};

function kindCell(r){
  const kind = r.signal_kind || "";
  const w = num(r.horizon_weeks), n = num(r.hist_patterns), h = num(r.hist_hits);
  let horizon;
  if (w !== null && n !== null && n >= 4) {
    horizon = `<span class="sm">חציון ${w} שבועות</span>
               <span class="sm">${h} מתוך ${n} הגיעו ליעד</span>`;
  } else if (n !== null && n > 0) {
    horizon = `<span class="sm">מדגם קטן (${n} תבניות)</span>`;
  } else {
    horizon = `<span class="sm">אין תבניות קודמות</span>`;
  }
  return `<td><span class="tag t-${KINDCLASS[kind]||'neutral'}">${esc(r.direction||"לונג")} · ${esc(kind)}</span>${horizon}</td>`;
}

function stopCell(r){
  const sp = num(r.stop_price), pc = num(r.stop_pct);
  if (sp === null) return `<td class="num">—</td>`;
  return `<td class="num"><span class="down">${sp.toFixed(2)}</span>
    <span class="sm ltr">${pc === null ? "" : pc.toFixed(1) + "%"}</span></td>`;
}

function rrCell(r){
  if (r.signal_kind === "פסילה") return `<td class="num">—</td>`;
  const v = num(r.risk_reward);
  if (v === null) return `<td class="num">—</td>`;
  const cls = v >= 2 ? "up" : (v >= 1 ? "" : "down");
  const note = v < 1 ? `<span class="sm">לא משתלם</span>` : "";
  return `<td class="num"><span class="${cls}">${v.toFixed(2)}</span>${note}</td>`;
}

function sizeCell(r){
  // מניה שנפסלה בשלב האיכות לא מקבלת גודל פוזיציה. להציג לה כמות פירושו
  // להזמין פתיחת עסקה במניה שהרגע נפסלה.
  if (r.signal_kind === "פסילה")
    return `<td class="num">—<span class="sm">נפסלה</span></td>`;
  const price = num(r.price), stop = num(r.stop_price);
  const port = num(document.getElementById("port").value);
  const riskPct = num(document.getElementById("risk").value);
  if (price === null || stop === null || stop >= price || !port || !riskPct)
    return `<td class="num">—</td>`;
  const perShare = price - stop;
  let shares = Math.floor((port * riskPct / 100) / perShare);
  if (shares < 1) return `<td class="num">—<span class="sm">הסיכון למניה גדול מדי</span></td>`;

  // סטופ צמוד מייצר כמות עצומה. תקרת ריכוזיות מונעת חצי תיק במניה אחת.
  const capped = Math.floor((port * MAX_POSITION_PCT / 100) / price);
  const hitCap = capped < shares;
  if (hitCap) shares = capped;
  if (shares < 1) return `<td class="num">—<span class="sm">מעבר לתקרת הריכוזיות</span></td>`;

  const exposure = shares * price;
  const note = hitCap
    ? `<span class="sm">הוגבל לתקרת ${MAX_POSITION_PCT}%</span>`
    : `<span class="sm ltr">${(100 * exposure / port).toFixed(0)}% of port</span>`;
  return `<td class="num">${shares}
    <span class="sm ltr">$${Math.round(exposure).toLocaleString("en-US")}</span>${note}</td>`;
}

function qualityCell(r){
  const q = num(r.quality_score);
  const red = (r.red_flag || "").trim();
  const bits = [];
  if (r.beneish_m !== "" && r.beneish_m !== undefined)
    bits.push(`<span class="sm ltr">Beneish ${num(r.beneish_m)?.toFixed(2)}</span>`);
  if (r.altman_z !== "" && r.altman_z !== undefined)
    bits.push(`<span class="sm ltr">Altman ${num(r.altman_z)?.toFixed(2)}</span>`);
  else if (r.altman_flag === "לא רלוונטי")
    bits.push(`<span class="sm">אלטמן לא רלוונטי</span>`);
  if (num(r.ebit_ev) !== null)
    bits.push(`<span class="sm ltr">EBIT/EV ${(100*num(r.ebit_ev)).toFixed(1)}%</span>`);
  if (num(r.momentum_12_1) !== null)
    bits.push(`<span class="sm ltr">Mom ${(100*num(r.momentum_12_1)).toFixed(0)}%</span>`);

  const head = red
    ? `<span class="tag t-bad">${esc(red)}</span>`
    : (q === null ? "—" : `<span class="tag t-${q>=66?'good':q>=33?'warm':'neutral'}">${q}</span>`);
  return `<td>${head}${bits.join("")}</td>`;
}

function grahamCell(r){
  const mos = num(r.margin_of_safety);
  const f = r.fscore === "" ? "" : `<span class="sm ltr">F ${r.fscore}/9</span>`;
  const m = mos === null ? "—" : (mos*100).toFixed(1) + "%";
  return `<td>${esc(r.graham_mode)}<span class="sm ltr">MoS ${m}</span>${f}</td>`;
}

function render(){
  const q = document.getElementById("q").value.trim().toLowerCase();
  const sf = document.getElementById("sig").value;
  const rf = document.getElementById("reg").value;
  const kf = document.getElementById("kind").value;

  let rows = DATA.filter(r => {
    if (sf && r.signal !== sf) return false;
    if (rf && r.regime !== rf) return false;
    if (kf && r.signal_kind !== kf) return false;
    if (q && !((r.ticker||"").toLowerCase().includes(q) || (r.name||"").toLowerCase().includes(q))) return false;
    return true;
  });

  rows.sort((a,b) => {
    let x = a[sortKey], y = b[sortKey];
    const nx = num(x), ny = num(y);
    if (nx !== null && ny !== null) { x = nx; y = ny; }
    else { x = String(x ?? ""); y = String(y ?? ""); }
    if (x < y) return -1*sortDir;
    if (x > y) return  1*sortDir;
    return String(a.ticker).localeCompare(String(b.ticker));
  });

  document.getElementById("tb").innerHTML = rows.map(r => {
    const cls = SIGCLASS[r.signal] || (r.signal_kind === "פסילה" ? "bad" : "neutral");
    const d200 = num(r.dist_sma200_pct);
    return `<tr>
      <td><span class="tag t-${cls}">${esc(r.signal)}</span></td>
      ${kindCell(r)}
      <td class="tk">${esc(r.ticker)}</td>
      <td class="name">${esc(r.name)}<span class="sm">${esc(r.sector)}</span></td>
      <td class="num">${num(r.price)?.toFixed(2) ?? "—"}</td>
      ${rsiCell(r)}
      ${targetCell(r)}
      ${stopCell(r)}
      ${rrCell(r)}
      ${sizeCell(r)}
      ${derivCell(r)}
      <td class="num"><span class="${d200>=0?'up':'down'}">${d200===null?"—":(d200>0?"+":"")+d200.toFixed(1)+"%"}</span>
        <span class="sm ltr">ADX ${num(r.adx)?.toFixed(1) ?? "—"}</span>
        <span class="sm ltr">ATR ${num(r.atr_pct)?.toFixed(2) ?? "—"}%</span></td>
      ${qualityCell(r)}
      ${grahamCell(r)}
    </tr>`;
  }).join("");

  document.getElementById("none").hidden = rows.length > 0;
}

document.querySelectorAll("th[data-k]").forEach(th => {
  th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (k === sortKey) sortDir *= -1;
    else { sortKey = k; sortDir = (k === "signal_rank" || k === "ticker" || k === "name") ? 1 : -1; }
    document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
    th.querySelector(".arrow").textContent = sortDir === 1 ? "▲" : "▼";
    render();
  });
});
["q","sig","reg","kind","port","risk"].forEach(id => document.getElementById(id)
  .addEventListener("input", () => { saveCalc(); render(); }));

// נוחות לגולש בלבד: המספרים נשמרים בדפדפן שלו ולא עוזבים אותו.
function saveCalc(){
  try {
    localStorage.setItem("graham_calc",
      JSON.stringify({p: document.getElementById("port").value,
                      r: document.getElementById("risk").value}));
  } catch (e) {}
}
try {
  const saved = JSON.parse(localStorage.getItem("graham_calc") || "null");
  if (saved && saved.p) document.getElementById("port").value = saved.p;
  if (saved && saved.r) document.getElementById("risk").value = saved.r;
} catch (e) {}

render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="tech_results.csv")
    ap.add_argument("--out", default="docs/technical.html")
    args = ap.parse_args()

    if os.path.exists(args.infile):
        df = pd.read_csv(args.infile, keep_default_na=False)
    else:
        df = pd.DataFrame()

    rows = df.to_dict(orient="records") if not df.empty else []
    total = len(rows)

    counts = {}
    for r in rows:
        counts[r.get("signal", "")] = counts.get(r.get("signal", ""), 0) + 1

    order = ["אזור כניסה", "היפוך חיובי טרי", "מעבר משטר", "בתמיכה, ממתין למומנטום",
             "מגמה תקינה", "איתות יציאה", "מתוח", "שבר את תעלת השורי",
             "מעבר, לא ברור", "ריבאונד בשוק דובי", "מגמה שלילית"]
    present = [s for s in order if counts.get(s)]
    present += sorted(k for k in counts
                      if k and k not in order and k.startswith(DISQUALIFIED_PREFIX))

    kinds = {}
    for r in rows:
        kinds[r.get("signal_kind", "")] = kinds.get(r.get("signal_kind", ""), 0) + 1
    bulls = sum(1 for r in rows if r.get("regime") == "שורי")

    def _rr(r):
        try:
            return float(r.get("risk_reward"))
        except (TypeError, ValueError):
            return None
    worthwhile = sum(1 for r in rows
                     if r.get("signal_kind") == "כניסה" and (_rr(r) or 0) >= 2)

    stats = [("מניות בבדיקה", total),
             ("נפסלו באיכות", kinds.get("פסילה", 0)),
             ("איתותי כניסה", kinds.get("כניסה", 0)),
             ("מהם עם יחס 2 ומעלה", worthwhile),
             ("איתותי יציאה", kinds.get("יציאה", 0)),
             ("במשטר שורי", bulls)]
    for s in present[:2]:
        stats.append((s, counts[s]))
    stats_html = "".join(
        f'<div class="stat"><span class="n">{v}</span><span class="l">{html.escape(k)}</span></div>'
        for k, v in stats)

    notes_html = "".join(
        f"<h3>{html.escape(t)}</h3><p>{html.escape(b)}</p>" for t, b in BOOK_NOTES)

    sig_opts = "".join(
        f'<option value="{html.escape(s)}">{html.escape(s)} ({counts[s]})</option>'
        for s in present)

    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    page = (PAGE
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__SIGCLASS__", json.dumps(SIGNAL_CLASS, ensure_ascii=False))
            .replace("__NOTES__", notes_html)
            .replace("__SIG_OPTS__", sig_opts)
            .replace("__STATS__", stats_html)
            .replace("__UPDATED__", updated)
            .replace("__COUNT__", str(total)))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[done] נכתב דף עם {total} מניות אל {args.out}")


if __name__ == "__main__":
    main()
