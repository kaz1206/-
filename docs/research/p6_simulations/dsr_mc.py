import numpy as np, sys
from scipy.stats import norm, skew, kurtosis
G=0.5772156649015329
def psr(sr,n,g3,g4,sr0=0.0):
    v=1-g3*sr+(g4-1)/4*sr*sr
    return norm.cdf((sr-sr0)*np.sqrt(n-1)/np.sqrt(v))
def sr0(N,V):
    return np.sqrt(V)*((1-G)*norm.ppf(1-1/N)+G*norm.ppf(1-1/(N*np.e)))
def run(R,N,T,rho,seed,mu=0.0):
    rng=np.random.default_rng(seed); p=d=0
    for _ in range(R):
        f=rng.standard_normal((T,1)); e=rng.standard_normal((T,N))
        x=mu+np.sqrt(rho)*f+np.sqrt(1-rho)*e
        s=x.mean(0)/x.std(0,ddof=1); k=int(np.argmax(s))
        g3=skew(x[:,k]); g4=kurtosis(x[:,k],fisher=False)
        V=s.var(ddof=1)
        p+=psr(s[k],T,g3,g4)>=0.95; d+=psr(s[k],T,g3,g4,sr0(N,V))>=0.95
    return p/R,d/R
for rho in (0.0,0.3,0.6,0.9):
    print(rho, run(int(sys.argv[1]),100,500,rho,20261011))
