import numpy as np, sys
from scipy.stats import norm, skew, kurtosis
from scipy.signal import lfilter
G=0.5772156649015329
def psr(sr,n,g3,g4,sr0=0.0):
    v=1-g3*sr+(g4-1)/4*sr*sr
    return norm.cdf((sr-sr0)*np.sqrt(n-1)/np.sqrt(v))
def sr0(N,V): return np.sqrt(V)*((1-G)*norm.ppf(1-1/N)+G*norm.ppf(1-1/(N*np.e)))
def rates(gen,R,seed):
    rng=np.random.default_rng(seed); p=d=0
    for _ in range(R):
        x=gen(rng); T,N=x.shape
        s=x.mean(0)/x.std(0,ddof=1); k=int(np.argmax(s))
        g3=skew(x[:,k]); g4=kurtosis(x[:,k],fisher=False)
        p+=psr(s[k],T,g3,g4)>=0.95; d+=psr(s[k],T,g3,g4,sr0(N,s.var(ddof=1)))>=0.95
    return round(p/R,4), round(d/R,4)
T,N=500,100
def clusters(K,rw):
    def g(rng):
        f=rng.standard_normal((T,K)); e=rng.standard_normal((T,N))
        return np.sqrt(rw)*f[:,np.arange(N)%K]+np.sqrt(1-rw)*e
    return g
def ar(phi,rho=0.0):
    def g(rng):
        f=rng.standard_normal((T,1)); e=rng.standard_normal((T,N)); x=np.sqrt(rho)*f+np.sqrt(1-rho)*e
        return lfilter([1],[1,-phi],x,axis=0)
    return g
def garch_like(rho=0.0):
    def g(rng):
        vol=np.exp(np.cumsum(rng.normal(0,0.1,(T,1)),axis=0)); vol/=vol.mean()
        f=rng.standard_normal((T,1)); e=rng.standard_normal((T,N)); x=(np.sqrt(rho)*f+np.sqrt(1-rho)*e)*vol
        return x
    return g
R=int(sys.argv[1])
for name,g in [("clusters K=5 rho_w=0.9",clusters(5,0.9)),("clusters K=2 rho_w=0.95",clusters(2,0.95)),
               ("AR phi=0.2",ar(0.2)),("AR phi=0.2 rho=0.9",ar(0.2,0.9)),("AR phi=0.4 rho=0.9",ar(0.4,0.9)),
               ("vol regimes rho=0.9",garch_like(0.9))]:
    print(name, rates(g,R,20261011))
