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


def test_skill_bundle_copies_references_but_not_runtime_files(tmp_path, monkeypatch):
    from tools import build_windows_package as build

    source = tmp_path / "source"
    for name in build.RESEARCH_SKILL_FILES:
        path = source / "skills" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    (source / "skills" / "du-jinlong-research-method" / "private-notes.json").write_text("private")
    monkeypatch.setattr(build, "ROOT", source)
    destination = tmp_path / "package"
    copied = build._copy_research_skills(destination)
    assert {p.relative_to(destination / "skills").as_posix() for p in copied} == set(build.RESEARCH_SKILL_FILES)
    assert all(p.read_text(encoding="utf-8") == p.relative_to(destination / "skills").as_posix() for p in copied)
    assert not list(destination.rglob("private-notes.json"))
    (source / "skills" / build.RESEARCH_SKILL_FILES[-1]).unlink()
    with pytest.raises(RuntimeError, match="required package resource is missing"):
        build._copy_research_skills(tmp_path / "incomplete")
    assert not (tmp_path / "incomplete").exists()


def test_guidance_reference_is_shipped_and_installed(tmp_path):
    from tools.build_windows_package import _copy_research_skills, RESEARCH_SKILL_FILES

    assert len(RESEARCH_SKILL_FILES) == len(set(RESEARCH_SKILL_FILES))
    copied = _copy_research_skills(tmp_path)
    guide = tmp_path / "skills/tw-stock-research/references/research-guidance-v1.md"
    assert guide in copied
    skill = (tmp_path / "skills/tw-stock-research/SKILL.md").read_text(encoding="utf-8")
    assert "references/research-guidance-v1.md" in skill
    assert "evidence-record" in guide.read_text(encoding="utf-8")
    installer = (WINDOWS / "tw-stock-predictor.iss").read_text(encoding="utf-8")
    assert installer.count('Source: "{#TW_STOCK_BUILD_ROOT}\\skills\\tw-stock-research\\references\\*.md"; DestDir: "{app}\\skills\\tw-stock-research\\references"') == 1


def test_manifest_disambiguates_skill_names_and_detects_reference_tamper(tmp_path):
    from tools.build_windows_package import _copy_research_skills
    from src.runtime.manifest import build_external_distribution_manifest
    from tools.validate_windows_package import _validate_distribution_manifest

    installer = tmp_path / "installer" / "setup.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"fixture")
    root = tmp_path / "executables"
    artifacts = _copy_research_skills(root)
    manifest = tmp_path / "distribution-manifest.json"
    build_external_distribution_manifest(installer, app_version="test", build_sha="test",
                                         artifact_paths=artifacts, artifact_root=root, output_path=manifest)
    assert _validate_distribution_manifest(manifest, tmp_path)["artifacts"]
    artifacts[-1].write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _validate_distribution_manifest(manifest, tmp_path)


@pytest.mark.parametrize("relative", ["../outside/SKILL.md", "C:/outside/SKILL.md", "skills/wrong-name.md"])
def test_distribution_rejects_invalid_relative_artifact_paths(tmp_path, relative):
    from src.runtime.manifest import build_external_distribution_manifest
    from tools.validate_windows_package import _validate_distribution_manifest

    installer = tmp_path / "installer" / "setup.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"fixture")
    root = tmp_path / "executables"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("fixture")
    manifest = tmp_path / "distribution-manifest.json"
    data = build_external_distribution_manifest(installer, app_version="test", build_sha="test",
                                               artifact_paths=[skill], artifact_root=root)
    data["artifacts"][0]["relative_path"] = relative
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="relative path is invalid"):
        _validate_distribution_manifest(manifest, tmp_path)


def test_offline_payload_check_detects_missing_method_reference(tmp_path, monkeypatch):
    from tools.build_windows_package import _copy_research_skills
    from tools import validate_windows_package as validate
    from src.runtime.manifest import sha256_file

    files = _copy_research_skills(tmp_path)
    records = [{"path": p.relative_to(tmp_path).as_posix(), "sha256": sha256_file(p)} for p in files]
    monkeypatch.setattr(validate, "_validate_ondir_bundle", lambda *args: True)
    assert validate._validate_research_payload(tmp_path, records)["skill"]
    files[-1].unlink()
    with pytest.raises(RuntimeError, match="resource missing or changed"):
        validate._validate_research_payload(tmp_path, records)


@pytest.mark.parametrize("configured,expected", [(None, True), ("false", False)])
def test_packaged_server_enables_reviewed_earnings_with_explicit_fallback(monkeypatch, configured, expected):
    import runpy
    from src.services.earnings_public_data_service import earnings_enabled

    main = runpy.run_path(str(WINDOWS / "server_entry.py"))["main"]
    settings = object()
    monkeypatch.setenv("RESEARCH_EARNINGS_V2_ENABLED", configured or "false")
    if configured is None:
        monkeypatch.delenv("RESEARCH_EARNINGS_V2_ENABLED")
    monkeypatch.setattr("sys.argv", ["packaged-server"])
    monkeypatch.setitem(main.__globals__, "_packaged_settings", lambda user_root: settings)
    calls = []

    def start(actual):
        assert actual is settings
        calls.append(earnings_enabled())
        return 0

    monkeypatch.setitem(main.__globals__, "run_server", start)
    assert main() == 0
    assert calls == [expected]
