import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.services.wave_pivot_lab import run_manifest, replay_fixture
from src.services.rule_registry import RuleRegistry
from src.domain.valuation import normalize_utc_timestamp
from tools.audit_wave_qualification import main

ROOT = Path(__file__).parent / "fixtures/wave_pivots"


def fixture():
    return json.loads((ROOT / "synthetic.json").read_text())


def test_pinned_fixture_prefix_replay_and_no_formal_outputs():
    result = run_manifest(ROOT / "manifest.json")
    item = result["results"][0]
    assert item["prefixes_checked"] == len(fixture()["rows"])
    assert item["pivots"]
    assert item["parameters"] == dict(lookback=5, confirmation_bars=3)
    assert item["historical_availability"] == "not_asserted"
    assert item["evidence_level"] == "C" and item["project_operationalization"] is True
    assert item["official_affiliation"] is False and item["implementation_mode"] == "project_operationalization"
    observed = normalize_utc_timestamp(fixture()["observed_at"], "observed_at")
    assert all(p["known_at"] == observed and not p["usable_as_anchor"] for p in item["pivots"])
    assert not {"targets", "wave3", "anchors"} & set(item)


def test_ambiguous_same_session_ties_and_unconfirmed_tail():
    value = fixture()
    value["rows"][6].update(high=1000, low=1)
    result = replay_fixture(value, "a" * 64)
    same = [p for p in result["pivots"] if p["pivot_date"] == value["rows"][6]["date"]]
    assert len(same) == 2 and all(p["order_status"] == "ambiguous_same_session" for p in same)
    assert all(p["pivot_date"] not in [r["date"] for r in value["rows"][-3:]] for p in result["pivots"])
    tied = copy.deepcopy(value)
    for row in tied["rows"]: row.update(open=100, high=101, low=99, close=100)
    assert replay_fixture(tied, "b"*64)["pivots"] == []


@pytest.mark.parametrize("kind", ["real", "unordered", "future", "zero", "duplicate", "oversized"])
def test_invalid_or_real_fixture_is_rejected(kind):
    value = fixture()
    if kind == "real": value["synthetic"] = False
    if kind == "unordered": value["rows"].reverse()
    if kind == "future": value["observed_at"] = "2025-01-01T00:00:00Z"
    if kind == "zero": value["rows"][0]["volume"] = 0
    if kind == "duplicate": value["rows"][1]["date"] = value["rows"][0]["date"]
    if kind == "oversized": value["rows"] *= 10
    with pytest.raises(ValueError): replay_fixture(value, "a"*64)


def test_manifest_hash_path_and_output_no_overwrite(tmp_path, capsys):
    payload = (ROOT / "synthetic.json").read_bytes()
    (tmp_path / "data.json").write_bytes(payload)
    manifest = dict(contract_version="wave_pivot_manifest_v1", fixtures=[dict(path="data.json", sha256="bad")])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash_mismatch"): run_manifest(path)
    manifest["fixtures"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    path.write_text(json.dumps(manifest))
    output = tmp_path / "out"
    assert main(["--fixture-manifest", str(path), "--output-dir", str(output)]) == 0
    before = (output / "report.json").read_bytes()
    assert main(["--fixture-manifest", str(path), "--output-dir", str(output)]) == 2
    assert (output / "report.json").read_bytes() == before
    manifest["fixtures"][0]["path"] = str((tmp_path / "data.json").resolve())
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="path_or_size"): run_manifest(path)
    capsys.readouterr()


def test_experimental_rule_cannot_enter_core():
    rule = RuleRegistry().describe("PIVOT-EXP-01")
    assert rule["evidence_level"] == "C" and rule["project_operationalization"] is True
    assert "approved_anchor" in rule["forbidden_uses"]
