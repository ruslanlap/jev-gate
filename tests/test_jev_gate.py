"""Offline tests for jev-gate answer validation + URL parsing + state building.

Live test (needs OPENROUTER_API_KEY) is marked `live` and skipped otherwise.
Fixtures: microsoft/winget-pkgs#431811 — real PR, real Jev answers captured 2026-09-21.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import jev_gate

FIX = Path(__file__).parent


def load(name):
    return json.loads((FIX / name).read_text())


def valid_answers():
    a = load("fixture_response_431811.json")["answers"]
    return json.loads(json.dumps(a))


# ---------------------------------------------------------------- validation

def test_validate_accepts_real_captured_answers():
    jev_gate.validate(valid_answers())


def test_validate_rejects_unknown_choice():
    a = valid_answers()
    a["primary_blocker"]["choice"] = "rm -rf /"
    with pytest.raises(ValueError, match="choice"):
        jev_gate.validate(a)


def test_validate_rejects_choice_outside_ids():
    a = valid_answers()
    a["next_action"]["choice"] = "delete_repo"  # plausible id, not in set
    with pytest.raises(ValueError):
        jev_gate.validate(a)


def test_validate_rejects_probability_key_mismatch():
    a = valid_answers()
    a["primary_blocker"]["probabilities"].pop("cla")
    with pytest.raises(ValueError, match="probabilities"):
        jev_gate.validate(a)


def test_validate_rejects_probs_not_summing_to_one():
    a = valid_answers()
    a["next_action"]["probabilities"]["merge"] = 0.5
    with pytest.raises(ValueError, match="probabilities"):
        jev_gate.validate(a)


def test_validate_rejects_nonfinite_and_out_of_range():
    a = valid_answers()
    a["merge_ready"]["noul"] = 1.5
    with pytest.raises(ValueError, match="merge_ready"):
        jev_gate.validate(a)
    a = valid_answers()
    a["cla_blocking"]["noul"] = float("nan")
    with pytest.raises(ValueError):
        jev_gate.validate(a)


def test_validate_rejects_missing_answer():
    a = valid_answers()
    del a["readiness"]
    with pytest.raises(ValueError, match="readiness"):
        jev_gate.validate(a)


def test_validate_rejects_score_out_of_range_and_bad_legend():
    a = valid_answers()
    a["readiness"]["score"] = 9
    with pytest.raises(ValueError, match="score"):
        jev_gate.validate(a)
    a = valid_answers()
    a["readiness"]["legend"] = {"0": "a", "1": "b"}
    with pytest.raises(ValueError, match="legend"):
        jev_gate.validate(a)


def test_validate_rejects_choice_not_argmax():
    a = valid_answers()
    a["primary_blocker"]["probabilities"]["certificate"] = 0.3
    a["primary_blocker"]["probabilities"]["validation"] = 0.7
    with pytest.raises(ValueError, match="argmax"):
        jev_gate.validate(a)


def test_validate_ignores_extra_answers():
    a = valid_answers()
    a["something_else"] = {"noul": 0.9}
    jev_gate.validate(a)  # extra keys are fine; we only enforce the contract we act on


# ------------------------------------------------------- url + state building

def test_parse_pr_url():
    assert jev_gate.parse_pr_url("https://github.com/o/r/pull/123")[:3] == ("o", "r", "123")
    assert jev_gate.parse_pr_url("https://github.com/o/r/pull/123/")[:3] == ("o", "r", "123")
    assert jev_gate.parse_pr_url("https://github.com/o/r/pull/123/files")[:3] == ("o", "r", "123")
    with pytest.raises(ValueError):
        jev_gate.parse_pr_url("https://gitlab.com/o/r/-/merge_requests/1")


def test_build_state_from_rest_fixtures():
    pr = load("fixture_pr_431811.json")
    reviews = load("fixture_reviews_431811.json")
    comments = load("fixture_comments_431811.json")
    # build_state exists as a pure function over REST payloads
    label, state = jev_gate.build_state(pr, reviews, comments)
    assert label == "microsoft/winget-pkgs#431811"
    for must in ["CERT_E_UNTRUSTEDROOT", "CHANGES_REQUESTED", "CLA", "certificate is the only blocker"]:
        assert must in state, must
    assert len(state) <= jev_gate.MAX_STATE_CHARS


# ------------------------------------------------------------------- live

@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OPENROUTER_API_KEY")
def test_live_jev_answers_match_expected_verdict():
    """Real PR #431811: reviewer confirmed cert is the only blocker → expect certificate verdict."""
    res = jev_gate.ask_jev(load("fixture_431811.json")["state"])
    answers = jev_gate.validate(res["answers"])
    assert answers["primary_blocker"]["choice"] == "certificate"
    assert answers["primary_blocker"]["confidence"] >= 0.9
    assert answers["merge_ready"]["noul"] <= 0.2
    assert answers["cla_blocking"]["noul"] <= 0.3
    assert answers["next_action"]["choice"] == "resign_installer"
    assert res["_latency_s"] < 5
