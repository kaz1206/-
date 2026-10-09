import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from robustlab.core import ids
from robustlab.core.models import InputSpec, InputType
from robustlab.core.params import ParamError, normalize_params

INPUTS = [
    InputSpec(name="Fast", type=InputType.INT),
    InputSpec(name="Lots", type=InputType.DOUBLE),
    InputSpec(name="Use", type=InputType.BOOL),
    InputSpec(name="Note", type=InputType.STRING),
    InputSpec(name="Tf", type=InputType.ENUM),
    InputSpec(name="Start", type=InputType.DATETIME),
]
PARAMS = {"Fast": 12, "Lots": 0.1, "Use": True, "Note": "日本語", "Tf": 16385, "Start": "2023-01-01 00:00:00"}


def test_normalize_values():
    n = normalize_params(PARAMS, INPUTS)
    assert n == {"Fast": 12, "Lots": "0.1", "Use": True, "Note": "日本語", "Tf": 16385, "Start": 1672531200}


def test_double_15_significant_digits_matches_hardware_value():
    n = normalize_params({**PARAMS, "Lots": 1.23456789012345}, INPUTS)
    assert n["Lots"] == "1.23456789012345"  # value confirmed on hardware (W3)


def test_datetime_forms_are_equivalent():
    a = normalize_params({**PARAMS, "Start": 1672531200}, INPUTS)
    b = normalize_params({**PARAMS, "Start": dt.datetime(2023, 1, 1)}, INPUTS)
    c = normalize_params({**PARAMS, "Start": dt.date(2023, 1, 1)}, INPUTS)
    assert a == b == c


@pytest.mark.parametrize(
    "patch",
    [
        {"Fast": "12"},
        {"Fast": True},
        {"Lots": float("nan")},
        {"Use": 1},
        {"Note": "a\nb"},
        {"Start": "2023-01-01T00:00:00+09:00"},
    ],
)
def test_type_errors(patch):
    with pytest.raises(ParamError):
        normalize_params({**PARAMS, **patch}, INPUTS)


def test_params_must_match_inputs_exactly():
    with pytest.raises(ParamError, match="missing"):
        normalize_params({k: v for k, v in PARAMS.items() if k != "Fast"}, INPUTS)
    with pytest.raises(ParamError, match="unknown"):
        normalize_params({**PARAMS, "Typo": 1}, INPUTS)


def test_harness_inputs_are_rejected_in_candidate_and_schema():
    with pytest.raises(ValueError):
        ids.candidate_id("sv_x", "EURUSD", "H1", {"RL_JobId": "jb_1"})
    with pytest.raises(ValueError):
        InputSpec(name="RL_JobId", type=InputType.STRING)


@given(st.dictionaries(st.text(min_size=1, max_size=8).filter(lambda k: not k.startswith("RL_")), st.integers(), min_size=1, max_size=6))
def test_candidate_id_independent_of_key_order(d):
    reversed_d = dict(reversed(list(d.items())))
    assert ids.candidate_id("sv_a", "EURUSD", "H1", d) == ids.candidate_id("sv_a", "EURUSD", "H1", reversed_d)


# 15 significant digits overflow only within 1e-15 of DBL_MAX; EA inputs never get there.
@given(st.floats(min_value=-1e300, max_value=1e300, allow_nan=False, width=64))
def test_double_normalization_is_stable_on_round_trip(x):
    spec = [InputSpec(name="X", type=InputType.DOUBLE)]
    once = normalize_params({"X": x}, spec)["X"]
    twice = normalize_params({"X": float(once)}, spec)["X"]
    assert once == twice


def test_ids_change_with_content_and_have_prefixes():
    sv = ids.strategy_version_id("a" * 64, None, "1")
    sv2 = ids.strategy_version_id("a" * 64, "b" * 64, "1")
    assert sv.startswith("sv_") and len(sv) == 19 and sv != sv2
    cd = ids.candidate_id(sv, "EURUSD", "H1", {"Fast": 12})
    rn1 = ids.run_id(cd, dt.date(2023, 1, 9), dt.date(2023, 1, 13), "h")
    rn2 = ids.run_id(cd, dt.date(2023, 1, 9), dt.date(2023, 1, 14), "h")
    assert cd.startswith("cd_") and rn1.startswith("rn_") and rn1 != rn2


def test_job_ids_are_unique_and_sortable():
    t = dt.datetime(2026, 10, 9, 12, 0, 0, 123000, tzinfo=dt.UTC)
    a, b = ids.new_job_id(t), ids.new_job_id(t)
    assert a != b and a.startswith("jb_20261009120000123_")
    later = ids.new_job_id(t + dt.timedelta(milliseconds=1))
    assert later > a or later[:21] > a[:21]
