"""Source qualification disclosure, never a wave detector or a price model.

The installed price adapter currently supplies unadjusted FinMind observations.
No user-provided 'verified' flag can promote those observations to confirmed
wave inputs. Keep external/manual hypotheses separate from source qualification.
"""
import os


def wave_support(summary):
    if os.getenv("RESEARCH_WAVE_ASSIST_ENABLED", "true").lower() != "true":
        return None
    data = (summary.get("public_data") or {}).get("TaiwanStockPrice") or {}
    rows = data.get("rows") or []
    known_source = data.get("source") == "FinMind" and data.get("dataset") == "TaiwanStockPrice"
    blockers = []
    if not rows:
        blockers.append("尚無可讀的歷史價量資料，請先更新資料。")
    if data.get("reason") == "snapshot_integrity_error":
        blockers.append("本機來源內容檢查失敗，不能使用這份快照。")
    if data.get("last_update_status") == "failed":
        blockers.append("上次更新失敗，目前只保留先前取得的資料。")
    if data.get("is_stale") or data.get("status") == "stale":
        blockers.append("來源標示資料過期，不能當作目前行情。")
    if data.get("excluded_rows") or any(r.get("zero_volume") or r.get("volume", 0) <= 0 for r in rows):
        blockers.append("含零成交量或已排除的異常列，不能跨過缺口判定轉折。")
    blockers.extend(["尚未核對區間內的完整交易日與停牌紀錄。",
                     "尚未核對除權息及其他公司行動造成的價格跳動。",
                     "尚無通過資格檢查的自動轉折確認器；價格高低點不等於已確認波浪。"])
    return {
        "contract_version": "wave_anchor_guidance_v1", "mode": "manual_candidate_support",
        "status": "quality_warning" if rows else "insufficient_data",
        "automatic_candidates_eligible": False, "automatic_candidates": [],
        "source": data.get("source") or "尚未取得", "dataset": data.get("dataset"),
        "snapshot_id": data.get("snapshot_id"), "source_sha256": data.get("raw_sha256"),
        "price_basis": "未還原權息的每日價格" if known_source else "價格口徑尚未核對",
        "first_date": min((r["date"] for r in rows), default=None),
        "last_date": max((r["date"] for r in rows), default=None),
        "observed_at": data.get("observed_at"), "checked_at": data.get("last_checked_at"),
        "blockers": blockers, "owner": "engineering" if rows or data.get("reason") == "snapshot_integrity_error" else "program",
        "next_action": "資料能力待補強；可先閱讀有來源的人工候選或保存部分研究。" if rows else "先更新歷史價量；有來源的人工候選可另外閱讀。",
        "confirmation_policy": "轉折發生日與確認時點必須分開；人工候選未提供確認時點時，不視為即時已確認轉折。",
        "approval_policy": "核准只接受人工假設，不會補足交易日、價格口徑或轉折確認證據。",
    }
