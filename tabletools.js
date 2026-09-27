/*
 * כלי טבלה משותפים לכל המסכים: שינוי רוחב עמודה בגרירה, ומיון בלחיצה על כותרת.
 *
 * - נטען אוטומטית על כל <table> בדף (חוץ מ-table.mini), גם על טבלאות שנבנות
 *   מחדש אחר כך (מסך המסחר מרענן את הטבלאות כל כמה שניות).
 * - מיון: לחיצה על כותרת ממיינת, לחיצה נוספת הופכת. מספרים כמספרים (כולל $, %,
 *   +/-), תאריכים כתאריכים, והשאר כטקסט. ריקים ו-"—" תמיד בסוף. תא יכול לקבוע
 *   ערך מיון משלו ב-data-sort. עמודה בלי כותרת, או th.nosort, לא ממוינת.
 *   טבלה שיש לה מיון משלה (th[data-k], בדפים הציבוריים) מקבלת רק שינוי רוחב.
 * - שורת פירוט (tr.panel) זזה יחד עם השורה שמעליה.
 * - המיון והרוחבים נשמרים בדפדפן (localStorage), לכל טבלה בנפרד.
 */
(function () {
  "use strict";
  const LS = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* פרטי/חסום */ } },
  };

  function injectCss() {
    if (document.getElementById("tt-css")) return;
    const st = document.createElement("style");
    st.id = "tt-css";
    st.textContent = `
      th.tt-th{position:relative}
      th.tt-sortable{cursor:pointer; user-select:none}
      th.tt-sortable:hover{color:var(--accent, #8a6a3b)}
      .tt-arrow{font-size:9px; opacity:.7; margin-inline-start:4px}
      .tt-rs{position:absolute; top:0; bottom:0; width:9px; cursor:col-resize; z-index:2; touch-action:none}
      .tt-rs::after{content:""; position:absolute; top:22%; bottom:22%; left:4px; width:1px; background:currentColor; opacity:0; transition:opacity .15s}
      th:hover > .tt-rs::after, .tt-rs.tt-on::after{opacity:.35}
      body.tt-resizing, body.tt-resizing *{cursor:col-resize !important; user-select:none !important}`;
    document.head.appendChild(st);
  }

  function keyFor(table) {
    const id = table.id || (table.tBodies[0] && table.tBodies[0].id) ||
      ((table.closest("[id]") || {}).id) || "t";
    return "tt:" + location.pathname + ":" + id;
  }

  // ערך מיון של תא: מספר, תאריך (כמספר), טקסט, או null לריק
  function cellValue(td) {
    if (!td) return null;
    const ds = td.getAttribute("data-sort");
    if (ds !== null && ds !== "") {
      const n = Number(ds);
      return isNaN(n) ? ds.toLowerCase() : n;
    }
    let t = td.textContent.replace(/[‎‏‪-‮]/g, "").trim();
    if (!t || t === "—" || t === "-" || t === "…") return null;
    let m = t.match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
    if (m) return Date.UTC(+m[1], m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
    m = t.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s*(\d{1,2}):(\d{2}))?/);
    if (m) return Date.UTC(+m[3], m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0));
    // מספר בתחילת התא: 42.73, -8.9%, +$38.00, $1,238.00, 3.6%
    m = t.replace(/,/g, "").replace(/−/g, "-").match(/^([+\-])?\s*\$?\s*([+\-])?\s*(\d+(?:\.\d+)?)/);
    if (m) return (m[1] === "-" || m[2] === "-" ? -1 : 1) * parseFloat(m[3]);
    return t.toLowerCase();
  }

  function groups(tbody) {
    const out = [];
    for (const tr of Array.from(tbody.rows)) {
      if (tr.classList.contains("panel") && out.length) out[out.length - 1].push(tr);
      else out.push([tr]);
    }
    return out;
  }

  function colIndex(th) {
    let i = 0;
    for (const c of th.parentNode.cells) { if (c === th) return i; i += c.colSpan || 1; }
    return -1;
  }

  function cellAt(tr, idx) {
    let i = 0;
    for (const c of tr.cells) { if (i === idx) return c; i += c.colSpan || 1; if (i > idx) return null; }
    return null;
  }

  function sortBody(table, st) {
    const tb = table.tBodies[0];
    if (!tb || !st || st.col == null) return;
    const gs = groups(tb);
    if (gs.length < 2 || gs.some(g => g[0].cells.length === 1 && g[0].cells[0].colSpan > 1)) return;
    const keyed = gs.map((g, i) => ({ g, i, v: cellValue(cellAt(g[0], st.col)) }));
    keyed.sort((a, b) => {
      const x = a.v, y = b.v;
      if (x === null && y === null) return a.i - b.i;
      if (x === null) return 1;
      if (y === null) return -1;
      const nx = typeof x === "number", ny = typeof y === "number";
      if (nx && ny) return (x - y) * st.dir || a.i - b.i;
      if (nx !== ny) return nx ? -1 : 1;
      return x.localeCompare(y, "he") * st.dir || a.i - b.i;
    });
    if (keyed.every((k, i) => k.i === i)) return;             // כבר ממוין - לא לגעת
    // המשקיף על השינויים מנותק בזמן הסידור, אחרת הסידור עצמו מפעיל אותו שוב
    if (table._ttObs) table._ttObs.disconnect();
    const frag = document.createDocumentFragment();
    keyed.forEach(k => k.g.forEach(tr => frag.appendChild(tr)));
    tb.appendChild(frag);
    if (table._ttObs) table._ttObs.observe(tb, { childList: true });
  }

  function paintArrows(table, st) {
    table.querySelectorAll("th .tt-arrow").forEach(a => a.remove());
    if (!st || st.col == null) return;
    const th = Array.from(table.tHead.rows[0].cells).find(c => colIndex(c) === st.col);
    if (!th) return;
    const a = document.createElement("span");
    a.className = "tt-arrow";
    a.textContent = st.dir === 1 ? "▲" : "▼";
    th.appendChild(a);
  }

  function enhance(table) {
    if (!table || table._tt || table.classList.contains("mini") || !table.tHead || !table.tHead.rows.length) return;
    table._tt = true;
    injectCss();
    const key = keyFor(table);
    const rtl = getComputedStyle(table).direction === "rtl";
    const ownSort = !!table.querySelector("th[data-k]");
    const heads = Array.from(table.tHead.rows[0].cells);
    const saved = LS.get(key) || {};
    let st = saved.sort || null;

    heads.forEach((th, n) => {
      th.classList.add("tt-th");
      if (getComputedStyle(th).position === "static") th.style.position = "relative";
      const w = saved.w && saved.w[n];
      if (w) { th.style.width = w + "px"; th.style.minWidth = w + "px"; }

      // גרירה לשינוי רוחב, בקצה הסוף של העמודה (בעברית: הצד השמאלי)
      if (n < heads.length - 1 || heads.length === 1) {
        const h = document.createElement("span");
        h.className = "tt-rs";
        h.style[rtl ? "left" : "right"] = "-4px";
        h.title = "גרור לשינוי רוחב";
        h.addEventListener("pointerdown", ev => {
          ev.preventDefault(); ev.stopPropagation();
          const x0 = ev.clientX, w0 = th.getBoundingClientRect().width;
          h.classList.add("tt-on"); document.body.classList.add("tt-resizing");
          h.setPointerCapture && h.setPointerCapture(ev.pointerId);
          const move = e => {
            const d = rtl ? x0 - e.clientX : e.clientX - x0;
            const nw = Math.max(36, Math.round(w0 + d));
            th.style.width = nw + "px"; th.style.minWidth = nw + "px";
          };
          const up = () => {
            h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up);
            h.removeEventListener("pointercancel", up);
            h.classList.remove("tt-on"); document.body.classList.remove("tt-resizing");
            table._ttJustResized = Date.now();
            const s = LS.get(key) || {};
            s.w = s.w || {};
            s.w[n] = Math.round(th.getBoundingClientRect().width);
            LS.set(key, s);
          };
          h.addEventListener("pointermove", move); h.addEventListener("pointerup", up);
          h.addEventListener("pointercancel", up);
        });
        h.addEventListener("dblclick", ev => {          // לחיצה כפולה: חזרה לרוחב אוטומטי
          ev.stopPropagation();
          th.style.width = ""; th.style.minWidth = "";
          const s = LS.get(key) || {};
          if (s.w) { delete s.w[n]; LS.set(key, s); }
        });
        th.appendChild(h);
      }

      if (ownSort) return;
      const label = th.textContent.trim();
      if (!label || th.classList.contains("nosort") || th.querySelector("input,button")) return;
      th.classList.add("tt-sortable");
      th.title = th.title || "מיון";
      th.addEventListener("click", ev => {
        if (ev.target.closest(".tt-rs, input, button, a")) return;
        const col = colIndex(th);
        if (st && st.col === col) st.dir = -st.dir;
        else {
          const vals = Array.from(table.tBodies[0] ? table.tBodies[0].rows : [])
            .filter(tr => !tr.classList.contains("panel"))
            .map(tr => cellValue(cellAt(tr, col))).filter(v => v !== null);
          const numeric = vals.filter(v => typeof v === "number").length >= vals.length / 2;
          st = { col, dir: numeric ? -1 : 1 };        // מספרים: מהגדול לקטן; טקסט: א-ת
        }
        const s = LS.get(key) || {};
        s.sort = st; LS.set(key, s);
        paintArrows(table, st);
        sortBody(table, st);
      });
    });

    // בדפים עם מיון משלהם: לחיצה שנגמרת גרירה לא תמיין בטעות
    table.tHead.addEventListener("click", ev => {
      if (table._ttJustResized && Date.now() - table._ttJustResized < 400) {
        ev.stopPropagation(); ev.preventDefault();
      }
    }, true);

    if (!ownSort) {
      paintArrows(table, st);
      sortBody(table, st);
      const tb = table.tBodies[0];
      if (tb) {
        table._ttObs = new MutationObserver(() => { if (st) sortBody(table, st); });
        table._ttObs.observe(tb, { childList: true });
      }
    }
  }

  function scan(root) {
    (root.tagName === "TABLE" ? [root] : Array.from(root.querySelectorAll ? root.querySelectorAll("table") : []))
      .forEach(enhance);
  }

  function start() {
    scan(document);
    new MutationObserver(muts => {
      for (const m of muts) for (const n of m.addedNodes) {
        if (n.nodeType === 1 && (n.tagName === "TABLE" || n.querySelector && n.querySelector("table"))) scan(n);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  window.TableTools = { enhance, cellValue };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
