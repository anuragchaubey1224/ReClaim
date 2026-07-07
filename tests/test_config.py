"""User-config tests (Phase 3a) — parsing, fail-safe degradation, and the safety invariant
that config can only ever *add* protection, never unlock a guarded name.

The load-bearing guarantees:
  * a missing/broken config degrades to the built-ins, never crashes and never to unsafe
    behavior (a typo becomes a warning, not silent data loss or an unprotected path);
  * a custom reclaimable unit flows through scan → classify like a built-in;
  * a custom protection is enforced at classify time *and* re-enforced at the Safety Gate;
  * protections always win (I2): a unit whose name is a protected directory is dropped, and a
    protection covering a built-in unit removes it.
"""

from __future__ import annotations

from pathlib import Path

from reclaim.core.classifier import classify, classify_scan
from reclaim.core.config import EMPTY, ReclaimConfig, build_ruleset, load_config
from reclaim.core.model import Candidate, Operation, Plan, Tier
from reclaim.core.rules import DEFAULT_RULESET
from reclaim.core.scanner import Scanner
from reclaim.safety.gate import SafetyGate


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


def _load(tmp_path: Path, body: str) -> ReclaimConfig:
    return load_config(_write(tmp_path, body))


# -- parsing: the happy path --------------------------------------------------

def test_missing_file_is_empty() -> None:
    assert load_config(Path("/no/such/dir/config.toml")) is EMPTY
    assert EMPTY.is_empty


def test_parses_units_and_protections(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "_build"
        regen = "mix compile"
        tier = "yellow"

        [[units]]
        name = ".cache-foo"
        regen = "foo build"

        [protect]
        dirs = ["research-data", "recordings"]
        files = ["*.pcap", "*.hdf5"]
    """)
    assert not cfg.warnings
    units = dict(cfg.units)
    assert units["_build"].tier is Tier.REGENERABLE_COSTLY
    assert units["_build"].regen_command == "mix compile"
    assert units[".cache-foo"].tier is Tier.REGENERABLE          # default tier is green
    assert cfg.protect_dirs == frozenset({"research-data", "recordings"})
    assert cfg.protect_file_globs == ("*.pcap", "*.hdf5")


def test_tier_aliases_and_regen_command_alias(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "a"
        regen = "x"
        tier = "GREEN"
        [[units]]
        name = "b"
        regen_command = "y"
        tier = "costly"
    """)
    units = dict(cfg.units)
    assert units["a"].tier is Tier.REGENERABLE
    assert units["b"].tier is Tier.REGENERABLE_COSTLY
    assert units["b"].regen_command == "y"                       # regen_command alias honored


def test_custom_label_optional(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "_build"
        regen = "x"
        label = "elixir build"
    """)
    assert dict(cfg.units)["_build"].label == "elixir build"


def test_single_string_protect_is_tolerated(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [protect]
        dirs = "just-one"
    """)
    assert cfg.protect_dirs == frozenset({"just-one"})
    assert not cfg.warnings


# -- fail-safe: bad input degrades to warnings, never a crash -----------------

def test_invalid_toml_yields_warning_not_crash(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "this is = = not valid toml"))
    assert cfg.is_empty
    assert cfg.warnings and "not valid TOML" in cfg.warnings[0]


def test_malformed_unit_entries_are_skipped_with_warnings(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "ok"
        regen = "build"

        [[units]]
        regen = "no name here"

        [[units]]
        name = "  "
        regen = "blank name"

        [[units]]
        name = "nocommand"

        [[units]]
        name = "badtier"
        regen = "x"
        tier = "purple"
    """)
    names = dict(cfg.units)
    assert set(names) == {"ok"}                                  # only the valid one survives
    joined = " ".join(cfg.warnings)
    assert "missing a non-empty 'name'" in joined
    assert "missing a 'regen' command" in joined
    assert "unknown tier" in joined


def test_bad_protect_shapes_warn(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        protect = "should be a table"
    """)
    assert cfg.is_empty
    assert any("[protect] must be a table" in w for w in cfg.warnings)


def test_non_string_protect_items_are_skipped(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [protect]
        dirs = ["keep", 5, "alsokeep"]
    """)
    assert cfg.protect_dirs == frozenset({"keep", "alsokeep"})
    assert any("non-string entry" in w for w in cfg.warnings)


def test_duplicate_unit_keeps_first(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "dup"
        regen = "first"
        [[units]]
        name = "dup"
        regen = "second"
    """)
    assert dict(cfg.units)["dup"].regen_command == "first"
    assert any("duplicate unit" in w for w in cfg.warnings)


# -- protections always win (I2) ----------------------------------------------

def test_unit_colliding_with_builtin_protection_is_dropped(tmp_path: Path) -> None:
    # `data` is a built-in protected dir name — it can never be registered as a unit.
    cfg = _load(tmp_path, """
        [[units]]
        name = "data"
        regen = "x"
    """)
    assert not cfg.units
    assert any("protections always win" in w for w in cfg.warnings)


def test_unit_colliding_with_user_protection_is_dropped(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "shared"
        regen = "x"
        [protect]
        dirs = ["shared"]
    """)
    assert not cfg.units
    assert cfg.protect_dirs == frozenset({"shared"})
    assert any("protections always win" in w for w in cfg.warnings)


# -- build_ruleset: folding config into the active ruleset --------------------

def test_empty_config_returns_base_unchanged() -> None:
    assert build_ruleset(EMPTY) is DEFAULT_RULESET


def test_build_ruleset_registers_custom_unit(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [[units]]
        name = "_build"
        regen = "make"
        tier = "yellow"
    """)
    rs = build_ruleset(cfg)
    assert rs.is_reclaimable_unit("_build").tier is Tier.REGENERABLE_COSTLY
    # base is untouched — extension is non-mutating
    assert DEFAULT_RULESET.is_reclaimable_unit("_build") is None


def test_build_ruleset_applies_custom_protections(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [protect]
        dirs = ["research-data"]
        files = ["*.pcap"]
    """)
    rs = build_ruleset(cfg)
    assert rs.protect_reason("research-data", is_dir=True) == \
        "'research-data/' is protected by your config"
    assert rs.protect_reason("trace.pcap", is_dir=False) == \
        "matches a protected pattern in your config"


def test_protecting_a_builtin_unit_name_removes_it(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
        [protect]
        dirs = ["target"]
    """)
    rs = build_ruleset(cfg)
    assert rs.is_reclaimable_unit("target") is None              # no longer reclaimable
    assert rs.protect_reason("target", is_dir=True) is not None  # now protected


# -- end to end: config changes what the engine sees --------------------------

def test_custom_unit_recognized_by_scanner_and_classifier(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "_build" / "ebin").mkdir(parents=True)
    (proj / "_build" / "ebin" / "mod.beam").write_text("x" * 5_000)
    (proj / "src").mkdir()
    (proj / "src" / "app.ex").write_text("y" * 100)

    rs = build_ruleset(_load(tmp_path, """
        [[units]]
        name = "_build"
        regen = "mix compile"
        tier = "yellow"
    """))
    res = classify_scan(Scanner(ruleset=rs).scan(str(proj)), ruleset=rs)
    hit = {c.path.name: c for c in res.candidates}
    assert "_build" in hit
    assert hit["_build"].tier is Tier.REGENERABLE_COSTLY
    assert hit["_build"].regen_command == "mix compile"


def test_custom_protected_dir_is_not_a_candidate(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "node_modules" / "p").mkdir(parents=True)
    (proj / "node_modules" / "p" / "i.js").write_text("j" * 2_000)
    (proj / "recordings").mkdir()
    (proj / "recordings" / "take.wav").write_text("w" * 2_000)

    rs = build_ruleset(_load(tmp_path, """
        [protect]
        dirs = ["recordings"]
    """))
    res = classify_scan(Scanner(ruleset=rs).scan(str(proj)), ruleset=rs)
    names = {c.path.name for c in res.candidates}
    assert "node_modules" in names
    assert "recordings" not in names                            # protected → never a candidate


def test_classify_marks_custom_protected_dir_red(tmp_path: Path) -> None:
    rs = build_ruleset(_load(tmp_path, """
        [protect]
        dirs = ["research-data"]
    """))
    cand = Candidate(Path("/x/research-data"), "research-data", 10, 10, 1,
                     tier=Tier.REGENERABLE)
    verdict = classify(cand, project=None, ruleset=rs)
    assert verdict.tier is Tier.IRREPLACEABLE
    assert "protected by your config" in verdict.reason


# -- defense in depth: the Safety Gate honors config protections at apply time -

def _op(source: Path) -> Operation:
    return Operation(source=source, kind="node_modules", size_allocated=1000,
                     file_count=1, tier=Tier.REGENERABLE, regen_command="npm install")


def test_gate_without_config_would_approve(tmp_path: Path) -> None:
    nm = tmp_path / "proj" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "i.js").write_text("x")
    gate = SafetyGate().validate(Plan((_op(nm),)))
    assert gate.all_approved                                     # baseline: a plain nm passes


def test_gate_enforces_config_protection_at_apply_time(tmp_path: Path) -> None:
    """A user protection added to config must block removal at the gate, even for a name the
    built-ins consider reclaimable — the same TOCTOU re-check that guards git state (I6)."""
    nm = tmp_path / "proj" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "i.js").write_text("x")

    rs = build_ruleset(_load(tmp_path, """
        [protect]
        dirs = ["node_modules"]
    """))
    gate = SafetyGate(ruleset=rs).validate(Plan((_op(nm),)))
    assert not gate.approved.operations
    assert gate.has_rejections
    assert "protected by your config" in gate.rejected[0].reason
    assert nm.exists()                                          # gate is read-only; nothing moved


# -- CLI: the `reclaim config` command ----------------------------------------

def test_cli_config_init_then_show(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from reclaim.cli.app import app

    runner = CliRunner()
    home = tmp_path / "home"
    env = {"RECLAIM_HOME": str(home)}

    # scaffold
    r = runner.invoke(app, ["config", "--init"], env=env)
    assert r.exit_code == 0
    assert "wrote starter config" in r.output
    assert (home / "config.toml").exists()

    # a second --init does not clobber
    r2 = runner.invoke(app, ["config", "--init"], env=env)
    assert "already exists" in r2.output

    # the starter is fully commented → shows as "no custom rules"
    r3 = runner.invoke(app, ["config"], env=env)
    assert r3.exit_code == 0
    assert "no custom rules" in r3.output


def test_cli_config_renders_rules_and_warnings(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from reclaim.cli.app import app

    runner = CliRunner()
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("""
        [[units]]
        name = "_build"
        regen = "mix compile"
        tier = "yellow"
        [[units]]
        name = "bad"
        regen = "x"
        tier = "purple"
        [protect]
        dirs = ["research-data"]
    """)
    r = runner.invoke(app, ["config"], env={"RECLAIM_HOME": str(home)})
    assert r.exit_code == 0
    assert "_build" in r.output
    assert "research-data" in r.output
    assert "unknown tier" in r.output                           # the bad entry warns, not crashes


def test_cli_scan_surfaces_config_warning(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from reclaim.cli.app import app

    runner = CliRunner()
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("this = = not toml")
    target = tmp_path / "empty"
    target.mkdir()

    r = runner.invoke(app, ["scan", str(target)], env={"RECLAIM_HOME": str(home)})
    assert r.exit_code == 0                                     # scan still works
    assert "config:" in r.output and "not valid TOML" in r.output
