# Toy model: yearly Sharpe-like scores. Noise candidate: N(0,1) per year. True strategy: annual Sharpe s -> mean s per year.
import numpy as np
rng=np.random.default_rng(20261012)
N=10000; Y=5; R=20000
cuts=[2000,500,100,25,1]
def run(true_means):
    seq=0; whole=0; noise_seq=0
    for _ in range(R):
        x=rng.standard_normal((N,Y)); x[0]+=true_means  # candidate 0 is the real one
        alive=np.arange(N)
        for y,k in enumerate(cuts):
            sc=x[alive,y]; alive=alive[np.argsort(-sc)[:k]]
        seq+= alive[0]==0
        tot=x.sum(1); whole+= np.argmax(tot)==0
    return seq/R, whole/R
for name,m in [("Sharpe0.5 even",np.full(5,0.5)),("Sharpe1.0 even",np.full(5,1.0)),
               ("trend lumpy (mean 1.0: 2 big years, 3 flat-bad)",np.array([3.0,-0.33,2.0,-0.33,-0.33]))]:
    s,w=run(m); print(f"{name}: sequential picks it {s:.2%}, whole-5y best picks it {w:.2%}")
# noise: always 1 survivor by construction
