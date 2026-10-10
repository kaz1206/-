import numpy as np, sys, time
from scipy.signal import lfilter
from arch.bootstrap import StationaryBootstrap, IIDBootstrap, optimal_block_length
R=int(sys.argv[1]); B=int(sys.argv[2]); n=520; phi=0.5
rng=np.random.default_rng(20261011); t0=time.time()
cov={"SB_mean":0,"IID_mean":0,"SB_sharpe":0,"IID_sharpe":0}
sharpe=lambda x: x.mean()/x.std(ddof=1)
for _ in range(R):
    x=lfilter([1],[1,-phi],rng.standard_normal(n))
    b=max(1.0,float(optimal_block_length(x)["stationary"].iloc[0]))
    for name,bs in (("SB",StationaryBootstrap(b,x,seed=rng)),("IID",IIDBootstrap(x,seed=rng))):
        ci=bs.conf_int(lambda z: np.array([z.mean(), sharpe(z)]), reps=B, method="percentile", size=0.90)
        cov[f"{name}_mean"]+=ci[0,0]<=0<=ci[1,0]; cov[f"{name}_sharpe"]+=ci[0,1]<=0<=ci[1,1]
print({k:v/R for k,v in cov.items()}, round(time.time()-t0,1),"s")
