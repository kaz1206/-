import numpy as np
rng=np.random.default_rng(20261013)
N=10000; Y=5; R=20000
cases=[("noise",np.zeros(5)),("Sharpe0.5 even",np.full(5,0.5)),("Sharpe1.0 even",np.full(5,1.0)),
       ("trend lumpy",np.array([3.0,-0.33,2.0,-0.33,-0.33]))]
for name,m in cases:
    surv=0; pick_cut=0; pick_whole=0
    for _ in range(R):
        x=rng.standard_normal((N,Y)); x[0]+=m
        alive=np.arange(N)
        for y in range(Y):
            sc=x[alive,y]; k=int(round(len(alive)*0.8)); alive=alive[np.argsort(-sc)[:k]]
        s0 = 0 in alive; surv+=s0
        tot=x.sum(1)
        pick_whole += np.argmax(tot)==0
        pick_cut += s0 and alive[np.argmax(tot[alive])]==0
    print(f"{name}: survives 5 cuts {surv/R:.1%} | final=best total among survivors {pick_cut/R:.2%} | whole-5y best {pick_whole/R:.2%} | survivors {len(alive)}")
