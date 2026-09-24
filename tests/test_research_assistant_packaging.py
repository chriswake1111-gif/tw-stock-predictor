from pathlib import Path
import json

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS = REPO_ROOT / "packaging" / "windows"


def test_research_entry_delegates_to_assistant_cli():
    source = (WINDOWS / "research_entry.py").read_text(encoding="utf-8")
    assert "from src.research_assistant.cli import main" in source
    assert "raise SystemExit(main())" in source


def test_research_spec_is_independent_console_onedir():
    source = (WINDOWS / "tw_stock_predictor_research.spec").read_text(encoding="utf-8")
    assert 'research_entry.py' in source
    assert 'name="tw-stock-research"' in source
    assert "console=True" in source
    assert 'collect_submodules("src.research_assistant")' in source


def test_installer_places_research_exe_and_skill_at_stable_relative_paths():
    source = (WINDOWS / "tw-stock-predictor.iss").read_text(encoding="utf-8")
    assert 'DestDir: "{app}\\research"' in source
    assert 'DestDir: "{app}\\skills\\tw-stock-research"' in source
    assert 'tw-stock-research\\*' in source
    assert 'SKILL.md' in source


def test_build_script_includes_research_spec_output_and_skill_hash():
    source = (REPO_ROOT / "tools" / "build_windows_package.py").read_text(encoding="utf-8")
    assert '"server", "launcher", "research"' in source
    assert 'tw-stock-research" / "tw-stock-research.exe' in source
    assert 'skills" / "tw-stock-research" / "SKILL.md' in source
    assert '"research_skill"' in source


def test_distribution_manifest_validates_every_listed_artifact_and_detects_skill_tampering(tmp_path):
    from tools.validate_windows_package import _validate_distribution_manifest
    from src.runtime.manifest import sha256_file

    package_root = tmp_path / "package"
    installer_dir = package_root / "installer"
    skill_path = package_root / "executables" / "skills" / "tw-stock-research" / "SKILL.md"
    installer_dir.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    installer = installer_dir / "setup.exe"
    installer.write_bytes(b"installer")
    skill_path.write_text("assistant skill v1\n", encoding="utf-8")
    manifest_path = package_root / "distribution-manifest.json"
    manifest_path.write_text(json.dumps({
        "manifest_version": "tw_stock_external_distribution_manifest_v1",
        "installer": {"filename": installer.name, "sha256": sha256_file(installer)},
        "artifacts": [{"filename": skill_path.name, "sha256": sha256_file(skill_path)}],
    }), encoding="utf-8")
    assert _validate_distribution_manifest(manifest_path, package_root)["installer"]
    skill_path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="distribution artifact checksum mismatch"):
        _validate_distribution_manifest(manifest_path, package_root)
