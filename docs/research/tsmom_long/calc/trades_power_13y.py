import numpy as np
from scipy.stats import norm
rng=np.random.default_rng(20261021)
def trades(price, L, H):
    idx=np.arange(L+1, len(price), H)
    sg=np.sign(price[idx-1]-price[idx-1-L]); sg=sg[sg!=0]
    return 0 if len(sg)==0 else int((sg[1:]!=sg[:-1]).sum())+1
for years, label in [(6.2,"6y (2008-2013)"),(13.0,"13y (2001-2013)")]:
    bars=int(years*252*6)
    for Hs in ([6,12,18,24,30],[30,60,90,120,150]):
        print(f"\n{label}: median trades / share<30, H={Hs}")
        for k in [1,2,3,6,9,12]:
            L=126*k; row=[]
            for H in Hs:
                t=[trades(np.cumsum(rng.standard_normal(L+2+bars)),L,H) for _ in range(300)]
                row.append(f"{np.median(t):>4.0f}/{np.mean(np.array(t)<30):.2f}")
            print(f"  L={k:>2}m ", " ".join(row))
def power(s,y,a=0.05): return 1-norm.cdf(norm.ppf(1-a)-s*np.sqrt(y))
print("\nP4 power, 13y discovery:", {s: f"{power(s,13):.0%}" for s in [0.3,0.5,0.7,1.0]}, " P4 bar SR:", round(norm.ppf(.95)/np.sqrt(13),2))
print("Whole-chain rough (P4 x P8a alpha .05..01, independent periods):")
for s in [0.3,0.5,0.7,1.0,1.5]:
    for y in (6.2,13):
        print(f"  SR {s} D={y}y: {power(s,y)*power(s,2.5,0.01):.0%} .. {power(s,y)*power(s,2.5):.0%}")
