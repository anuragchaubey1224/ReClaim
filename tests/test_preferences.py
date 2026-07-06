"""Preference-memory tests — the store, plus its enforcement in the classifier and gate.

Proves the invariant that matters: a saved "never touch" rule hard-protects a matching path
at classify time *and* is re-checked at apply time — enforced by the deterministic engine,
with no AI involved (I5/I7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from reclaim.core.classifier import classify
from reclaim.core.model import Candidate, Operation, Plan, Tier
from reclaim.core.preferences import Preference, PreferenceStore
from reclaim.safety.gate import SafetyGate


def _fixed_clock() -> datetime:
    return datetime(2026, 7, 6, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "preferences.json", clock=_fixed_clock)


# -- Preference.matches -------------------------------------------------------

def test_glob_matches_subtree() -> None:
    pref = Preference("~/work/**")
    assert pref.matches(Path.home() / "work" / "proj" / "node_modules")
    assert not pref.matches(Path.home() / "play" / "node_modules")


def test_bare_dir_protects_itself_and_subtree() -> None:
    pref = Preference("/data/secret")
    assert pref.matches("/data/secret")
    assert pref.matches("/data/secret/db/x.sqlite")
    assert not pref.matches("/data/secretive")          # not a path-boundary match


def test_expanduser_applied() -> None:
    assert Preference("~/work").matches(Path.home() / "work" / "x")


# -- PreferenceStore ----------------------------------------------------------

def test_empty_store(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.all() == ()
    assert s.matches("/anything") is None


def test_add_match_and_persist(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add("~/work/**", note="my stuff")
    hit = s.matches(Path.home() / "work" / "a" / "node_modules")
    assert hit is not None and hit.pattern == "~/work/**" and hit.note == "my stuff"
    assert hit.created == _fixed_clock().isoformat()
    # A fresh instance reads the same persisted rule (survives process restart).
    assert PreferenceStore(tmp_path / "preferences.json").matches(Path.home() / "work" / "z")


def test_add_dedupes_by_pattern(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add("~/work/**", note="first")
    s.add("~/work/**", note="second")
    rules = s.all()
    assert len(rules) == 1 and rules[0].note == "second"


def test_add_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _store(tmp_path).add("   ")


def test_remove(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add("~/work/**")
    assert s.remove("~/work/**") is True
    assert s.all() == ()
    assert s.remove("~/work/**") is False


# -- classifier enforcement ---------------------------------------------------

def test_classify_preference_hard_protects(tmp_path: Path) -> None:
    cand = Candidate(Path.home() / "work" / "app" / "node_modules", "node_modules",
                     100, 100, 1, tier=Tier.REGENERABLE, regen_command="npm install")
    s = _store(tmp_path)
    s.add("~/work/**", note="active")

    protected = classify(cand, None, s)
    assert protected.tier is Tier.IRREPLACEABLE
    assert "user preference" in protected.reason and "active" in protected.reason
    # Same candidate without the rule stays reclaimable.
    assert classify(cand, None).tier is Tier.REGENERABLE


# -- gate enforcement (apply-time re-check) -----------------------------------

def _real_op(tmp_path: Path) -> tuple[Operation, Path]:
    nm = tmp_path / "work" / "myapp" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x" * 100)
    op = Operation(source=nm, kind="node_modules", size_allocated=100, file_count=1,
                   tier=Tier.REGENERABLE, regen_command="npm install")
    return op, nm


def test_gate_rejects_preference_protected_op(tmp_path: Path) -> None:
    op, nm = _real_op(tmp_path)
    plan = Plan((op,))
    s = _store(tmp_path)
    s.add(str(tmp_path / "work") + "/**")

    gate = SafetyGate(preferences=s).validate(plan)
    assert gate.approved.is_empty
    assert any("user preference" in r.reason for r in gate.rejected)
    # Without the rule the same op is approved (proves the rule is what blocked it).
    assert not SafetyGate().validate(plan).approved.is_empty
