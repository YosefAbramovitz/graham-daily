import sys, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
import swing
from swing_sim import SPLIT
from portfolio_fix_sim import load, CACHE
d,C,H,L,V = load(CACHE)
sig=swing.signals(C,H,L,V).to_numpy() & d.spy_ok[:,None]
Cn=d.C; On=d.O; sp=d.spy_col
S,K=np.nonzero(sig[200:-1]); S=S+200
K=K[K!=sp] if False else K
m=K!=sp; S,K=S[m],K[m]
ins=d.idx[S]<SPLIT
res={}
for k in [1,2,3,5,7,10,15,20,25,30,40]:
    j=S+k   # entry at open of S+1, close of day k (k=1 -> close of entry day)
    ok=j<len(d.idx)
    r=Cn[j[ok],K[ok]]/On[S[ok]+1,K[ok]]-1
    rs=Cn[j[ok],sp]/On[S[ok]+1,sp]-1
    ex=r-rs; ii=ins[ok]
    res[k]=dict(IS=np.nanmean(ex[ii])*100, OOS=np.nanmean(ex[~ii])*100, IS_raw=np.nanmean(r[ii])*100, OOS_raw=np.nanmean(r[~ii])*100)
print("mean return from entry open to close of day k (%), stock minus SPY, no stop")
print(pd.DataFrame(res).T.round(2).to_string())
