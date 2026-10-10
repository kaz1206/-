import numpy as np, sys
from scipy.stats import norm, skew, kurtosis
from scipy.signal import lfilter
G=0.5772156649015329
def psr(sr,n,g3,g4,sr0=0.0):
    v=1-g3*sr+(g4-1)/4*sr*sr
    return norm.cdf((sr-sr0)*np.sqrt(n-1)/np.sqrt(v))
def sr0(N,V): return np.sqrt(V)*((1-G)*norm.ppf(1-1/N)+G*norm.ppf(1-1/(N*np.e)))
def r1(x):
    x=x-x.mean(); return float((x[1:]*x[:-1]).sum()/(x*x).sum())
def neff(n,rho): return n*(1-rho)/(1+rho) if rho>0 else n
def rates(gen,R,seed):
    rng=np.random.default_rng(seed); out=np.zeros(3)
    for _ in range(R):
        x=gen(rng); T,N=x.shape
        s=x.mean(0)/x.std(0,ddof=1); k=int(np.argmax(s))
        g3=skew(x[:,k]); g4=kurtosis(x[:,k],fisher=False); S0=sr0(N,s.var(ddof=1))
        a=psr(s[k],T,g3,g4,S0); b=psr(s[k],neff(T,r1(x[:,k])),g3,g4,S0)
        out+=[a>=0.95,b>=0.95,min(a,b)>=0.95]
    return np.round(out/R,4)
T,N=500,100
def ar(phi,rho=0.0,mu=0.0):
    def g(rng):
        f=rng.standard_normal((T,1)); e=rng.standard_normal((T,N)); x=np.sqrt(rho)*f+np.sqrt(1-rho)*e
        return lfilter([1],[1,-phi],x,axis=0)+mu
    return g
R=int(sys.argv[1])
print("cols: DSR, DSR_ac(n_eff), min")
for name,g in [("iid rho0",ar(0)),("AR0.2 rho0.9",ar(0.2,0.9)),("AR0.4 rho0.9",ar(0.4,0.9)),("AR0.6 rho0.9",ar(0.6,0.9)),("AR0.4 rho0.3",ar(0.4,0.3)),
               ("power: AR0.2 rho0.3 mu=0.12",ar(0.2,0.3,0.12)),("power: iid rho0.3 mu=0.12",ar(0.0,0.3,0.12))]:
    print(name, rates(g,R,20261011))
