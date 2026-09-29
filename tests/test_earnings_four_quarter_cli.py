import hashlib
import json

import pytest

from tools import audit_earnings_sources as cli


@pytest.fixture
def sample(tmp_path, monkeypatch):
    raw = b"fictional public source"
    (tmp_path / "source.pdf").write_bytes(raw)
    item = dict(symbol="9999.TW", key="sample", path="source.pdf", sha256=hashlib.sha256(raw).hexdigest(),
                url="https://example.org/financial.pdf", observed_at="2026-09-28T00:00:00Z")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([item]), encoding="utf8")
    monkeypatch.setattr(cli, "sources_for", lambda symbol: [item])
    def normalize(symbol, docs, observed):
        assert docs == {"sample": raw} and symbol == "9999.TW"
        return dict(status="available", value="2.75")
    monkeypatch.setattr(cli, "normalize_bundle", normalize)
    return manifest, item


def test_cli_is_local_sample_only_never_enables_or_imports_and_refuses_overwrite(sample, tmp_path, capsys):
    manifest, _ = sample
    output = tmp_path / "result.json"
    args = ["--four-quarter-manifest", str(manifest), "--output", str(output)]
    assert cli.main(args) == 0
    report = json.loads(output.read_text(encoding="utf8"))
    assert not report["calculation_enabled"] and report["four_quarter_sample"]["value"] == "2.75"
    assert "transport_not_authenticated" in report["sample_provenance"]
    original = output.read_bytes()
    assert cli.main(args) == 2 and output.read_bytes() == original


@pytest.mark.parametrize("change", ["duplicate", "different_company", "wrong_url", "extra_approval", "outside_directory", "wrong_hash"])
def test_invalid_manifest_cannot_grant_qualification(sample, change):
    manifest, item = sample
    items = [dict(item)]
    if change == "duplicate": items.append(dict(item))
    elif change == "different_company": items.append(dict(item, symbol="8888.TWO"))
    elif change == "wrong_url": items[0]["url"] = "https://evil.invalid"
    elif change == "extra_approval": items[0]["approved"] = True
    elif change == "outside_directory": items[0]["path"] = "../outside.pdf"
    else: items[0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(items), encoding="utf8")
    assert cli.main(["--four-quarter-manifest", str(manifest)]) == 2
