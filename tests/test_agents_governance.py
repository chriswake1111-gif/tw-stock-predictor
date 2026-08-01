from pathlib import Path


def test_agents_model_position_does_not_promote_legacy_rules_to_verified_core():
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    section = agents.split("## 3. 杜金龍模型定位", 1)[1].split("\n---", 1)[0]

    assert "一律以 `config/model_rules.yaml`" in section
    assert "不得因舊程式、舊文件或歷史回測存在" in section
    assert "固定費氏均線群、固定均線共振、20／30／50、7%～11% 與 EVA" in section
    assert "不得描述為 verified core" in section
    assert "杜金龍模型是本專案的核心技術循環引擎，主要涵蓋" not in section
