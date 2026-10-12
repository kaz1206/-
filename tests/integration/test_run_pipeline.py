"""scripts/run_pipeline.py end to end on the fake terminal: full P1-P7 path, and the stop on a P1 warning."""
import subprocess, sys, textwrap
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def test_runner(opt, monkeypatch, tmp_path):
    cfg = opt.req.parent.parent
    (cfg / "rq.yaml").write_text(textwrap.dedent("""
        strategy_file: strategies/smoke.yaml
        symbol: EURUSD
        timeframe: H4
        from_date: 2022-01-03
        to_date: 2023-01-02
        params: {InpFastPeriod: 15, InpSlowPeriod: 80, InpLots: 0.10}
        tester: {model: OHLC_M1, deposit: 10000, currency: USD, leverage: 100}
    """))
    st = (ROOT / "configs/studies/trend_ma_h4_v2.yaml").read_text().replace("../strategies/rl_smoketest.yaml", "strategies/smoke3.yaml")
    st = st.replace("2008-01-02", "2022-01-03").replace("2014-01-01", "2023-01-02")
    (cfg / "st.yaml").write_text(st)
    pl = tmp_path / "p.yaml"
    pl.write_text(f"name: t\nrequest: {opt.req}\nstudy: {cfg/'st.yaml'}\nprofile: swing_trend\nseed: 7\n")
    monkeypatch.setenv("FAKE_MODE", "ok"); monkeypatch.setenv("FAKE_OPT_MODE", "mixed")
    monkeypatch.setenv("FAKE_OPT_DAYS", "120"); monkeypatch.setenv("FAKE_OPT_EDGE", "1")
    p = subprocess.run([sys.executable, str(ROOT/"scripts/run_pipeline.py"), str(pl), "--workspace", str(opt.ws),
                        "--terminal", str(opt.terminal_cfg), "--partition", str(opt.partition), "--out-dir", str(tmp_path/"runs")],
                       capture_output=True, text=True, cwd=ROOT)
    print(p.stdout[-3000:]); print(p.stderr[-2000:])
    assert p.returncode == 0


def test_runner_stops_on_p1_warning(opt, monkeypatch, tmp_path):
    import json
    from robustlab.storage.db import Database
    cfg = opt.req.parent.parent
    monkeypatch.setenv("FAKE_MODE", "ok")
    assert opt.run().status == "SUCCEEDED"
    db = Database(opt.ws / "robustlab.sqlite")
    db.conn.execute("UPDATE job SET warnings_json = ?", (json.dumps(["TEST_WARNING"]),))  # temp test db only
    db.close()
    pl = tmp_path / "p.yaml"
    pl.write_text(f"name: t\nrequest: {opt.req}\nstudy: x.yaml\nprofile: swing_trend\nseed: 7\n")
    p = subprocess.run([sys.executable, str(ROOT/"scripts/run_pipeline.py"), str(pl), "--workspace", str(opt.ws),
                        "--terminal", str(opt.terminal_cfg), "--partition", str(opt.partition), "--out-dir", str(tmp_path/"runs")],
                       capture_output=True, text=True, cwd=ROOT)
    print(p.stdout[-800:])
    assert p.returncode == 2 and "TEST_WARNING" in p.stdout and "## P3" not in p.stdout
