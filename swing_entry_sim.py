"""
בדיקת נקודות הכניסה והסטופ מהקורס (פסגות, שיעורים 2, 6, 7, 9, 12) על הכלל של swing.py.

כניסות: למחרת בפתיחה (שלנו) / buy-stop מעל הגבוה של היום הקודם / סגירה מעל הגבוה
הקודם / נר היפוך (פטיש או OKR) ופריצת הגבוה שלו - עד 5 ימים אחרי האות.
סטופ: 1.5 ATR (שלנו) / מבני - מתחת לשפל התיקון. וגם: תוצאה לפי עומק התיקון
בפיבונאצ'י. הבחירה על 2016-2020, הבדיקה על 2021-2025.

    python swing_entry_sim.py [variants_cache]
"""
CACHE = __import__("sys").argv[1] if len(__import__("sys").argv) > 1 else "variants_cache"
import sys; sys.path.insert(0, '.')
from swing_sim import *
import swing
d = Data(Path(CACHE))
b = pd.read_pickle(Path(CACHE) / 'bars.pkl.gz'); cols = d.cols
f = lambda k: b[k].reindex(columns=cols).astype('float64')
C, H, L, O, V = f('close'), f('high'), f('low'), f('open'), f('volume')
sig = swing.signals(C, H, L, V).to_numpy()
A = d.atr; Hn, Ln, On, Cn = d.H, d.L, d.O, d.C
N = len(d.idx)


def run(e, k, entry, stop, days=15):
    risk = entry - stop
    if not (risk > 0):
        return None
    last = min(e + days - 1, N - 1); px = None; j = e
    for j in range(e, last + 1):
        o, l, c = On[j, k], Ln[j, k], Cn[j, k]
        if math.isnan(c):
            continue
        if j > e and not math.isnan(o) and o <= stop:
            px = o; break
        if l <= stop:
            px = stop; break
    if px is None:
        while math.isnan(Cn[j, k]) and j > e:
            j -= 1
        px = Cn[j, k]
    ret = px * (1 - COST) / (entry * (1 + COST)) - 1
    return j, ret, ret * entry / risk, risk / entry


body = (C - O).abs(); rng = (H - L).replace(0, np.nan)
hammer = ((np.minimum(O, C) - L) >= 2 * body) & ((C - L) / rng >= 0.6)
okr = (H > H.shift()) & (L < L.shift()) & (C > C.shift()) & (C > O)
rev = (hammer | okr).fillna(False).to_numpy()
swing_hi = H.rolling(40, min_periods=20).max()
swing_lo = L.rolling(100, min_periods=60).min()
retr = ((swing_hi - C) / (swing_hi - swing_lo)).to_numpy()


def trades(trigger, stopmode, W=5, market=True):
    out = []; busy = np.full(len(cols), -1)
    spyk = cols.index('SPY')
    for s in range(200, N - 2):
        if market and not d.spy_ok[s]:
            continue
        for k in np.flatnonzero(sig[s]):
            if k == spyk or busy[k] >= s:
                continue
            a = A[s, k]
            if not a > 0:
                continue
            e = None; entry = None
            if trigger == 'open':
                e = s + 1; entry = On[e, k]
            elif trigger == 'bstop':
                for j in range(s + 1, min(s + 1 + W, N)):
                    lvl = Hn[j - 1, k]
                    if Hn[j, k] >= lvl:
                        e = j; entry = max(On[j, k], lvl); break
            elif trigger == 'rev':
                for r in range(s, min(s + W, N - 1)):
                    if rev[r, k]:
                        j = r + 1
                        if Hn[j, k] >= Hn[r, k]:
                            e = j; entry = max(On[j, k], Hn[r, k])
                        break
            elif trigger == 'closeup':
                for j in range(s + 1, min(s + 1 + W, N - 1)):
                    if Cn[j, k] > Hn[j - 1, k]:
                        e = j + 1; entry = On[e, k]; break
            if e is None or not (entry and entry > 0):
                continue
            if stopmode == 'atr':
                stop = entry - 1.5 * a
            else:
                lo = np.nanmin(Ln[max(s - 5, 0):e, k]); stop = min(lo - 0.1 * a, entry - 0.5 * a)
            r = run(e, k, entry, stop)
            if r is None:
                continue
            busy[k] = r[0]
            out.append((s, k, r[0], r[1], r[2], r[3], e))
    return pd.DataFrame(out, columns=['s', 'k', 'x', 'ret', 'R', 'riskpct', 'e'])


base = None
for trig in ('open', 'bstop', 'closeup', 'rev'):
    for sm in ('atr', 'struct'):
        t = trades(trig, sm)
        i = summary(t[d.idx[t.s] < SPLIT], d); o = summary(t[d.idx[t.s] >= SPLIT], d)
        e_ = portfolio(d, t, rank='mom'); a = pstats(e_[e_.index < SPLIT]); bb = pstats(e_[e_.index >= SPLIT])
        print(f"{trig:8} {sm:6} IS n{i['n']:5d} R{i['exp_R']:+.3f} win{i['win']*100:3.0f}% ret{i['avg_ret']*100:+.2f}% | "
              f"OOS n{o['n']:5d} R{o['exp_R']:+.3f} t{o['t']:4.1f} win{o['win']*100:3.0f}% ret{o['avg_ret']*100:+.2f}% | "
              f"port IS{a[0]*100:+5.1f}% OOS{bb[0]*100:+5.1f}% DD{bb[1]*100:5.1f}% Sh{bb[2]:.2f}", flush=True)
        if trig == 'open' and sm == 'atr':
            base = t.copy()
base['retr'] = [retr[s, k] for s, k in zip(base.s, base.k)]
base['oos'] = d.idx[base.s] >= SPLIT
base['b'] = pd.cut(base.retr, [-1, 0.236, 0.382, 0.5, 0.618, 0.786, 5])
print(base.groupby(['b', 'oos'], observed=True).R.agg(['count', 'mean']).unstack().round(3))
ind = swing.indicators(C, H, L, V); q = swing.quality(ind).to_numpy()
base['q'] = [q[s, k] for s, k in zip(base.s, base.k)]
base['deep'] = base.retr > 0.786
print(base.groupby(['q', 'deep', 'oos']).R.agg(['count', 'mean']).unstack().round(3))
