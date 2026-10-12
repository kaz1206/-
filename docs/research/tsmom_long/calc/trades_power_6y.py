import numpy as np
from scipy.stats import norm
rng=np.random.default_rng(20261020)
# --- trade counts of the TSMom rule on driftless random walks (H4, 6 bars/day) ---
def trades(price, L, H):
    n=len(price); pos=0; t=0; cnt=0
    for i in range(L+1, n):
        cnt+=1
        if (cnt-1)%H: continue
        c1, c0 = price[i-1], price[i-1-L]
        tgt = 1 if c1>c0 else (-1 if c1<c0 else 0)
        if tgt and tgt!=pos:
            if pos: t+=1
            pos=tgt
    return t + (1 if pos else 0)   # closing deals incl. the end-of-test close
bars_D=1565*6; bars_V0=509*6; bars_V1=119*6
Ls=[126*k for k in range(1,13)]; Hs=[30,60,90,120,150]
R=200
res={}
for L in Ls:
    for H in Hs:
        tD=[];tV0=[];tV1=[]
        for _ in range(R):
            # pre-history of L bars is assumed available (to be checked on hardware)
            p=np.cumsum(rng.standard_normal(L+2+bars_D))
            tD.append(trades(p,L,H))
        res[(L,H)]=np.median(tD), np.mean(np.array(tD)<30)
print("median trades in 6y Discovery / share of paths with <30 trades (random walk)")
print("L(months)", " ".join(f"H={h:>3}" for h in Hs))
for L in Ls:
    print(f"{L//126:>3}       ", "  ".join(f"{res[(L,H)][0]:>4.0f}/{res[(L,H)][1]:.2f}" for H in Hs))
# --- analytic power ---
def power(sr_ann, years, alpha=0.05):
    z=sr_ann*np.sqrt(years)       # SR*sqrt(n) with daily SR = sr_ann/sqrt(252), n=252*years
    return 1-norm.cdf(norm.ppf(1-alpha)-z)
print("\nP(one pre-specified candidate passes PSR>=0.95):")
for s in [0.3,0.5,0.7,1.0,1.5]:
    print(f" true annual SR {s}: 6y Discovery {power(s,6.2):.0%} | P8a V0+V1 (2.5y, alpha .05) {power(s,2.5):.0%} | (alpha .01=Holm K=5 worst) {power(s,2.5,0.01):.0%}")
print("\nP4 bar (annual SR) for 6.2y:", norm.ppf(.95)/np.sqrt(6.2), " P8a bar 2.5y:", norm.ppf(.95)/np.sqrt(2.5))
