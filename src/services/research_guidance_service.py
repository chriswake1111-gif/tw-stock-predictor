"""Presentation guidance derived from evidence; does not mutate financial state."""
from datetime import datetime, timedelta, timezone
from src.domain.research_evidence import GUIDANCE_CONTRACT
from src.services.research_evidence_service import ResearchEvidenceService
from src.services.wave_anchor_guidance import wave_support


def build_guidance(summary, assumptions, evidence, fiscal_year=None):
    applied = summary.get("valuation_context", {})
    matrix = applied.get("target_matrix", []) if applied.get("status") == "available" else []
    approved = [a for a in assumptions if not a.get("superseded") and (a.get("approval") or {}).get("decision") == "approved"]
    years = sorted({int(a["fiscal_year"]) for a in approved if a.get("fiscal_year")})
    selected_year = fiscal_year if fiscal_year is not None else years[0] if len(years) == 1 else None
    items = evidence.get("all_items", evidence.get("items", []))
    eligible = []
    for item in items:
        try:
            ResearchEvidenceService.prepare_candidate(item)
            eligible.append(item)
        except (ValueError, KeyError, TypeError):
            pass
    candidate_years = sorted({i["fiscal_year"] for i in eligible if i.get("fiscal_year")})
    candidates = []
    for topic in ("eps", "pe", "anchor"):
        candidates.extend([i for i in eligible if i["kind"] == "candidate" and i["topic"] == topic
                           and i["review_status"] in {"reviewable", "limited"}
                           and (topic == "anchor" or i["fiscal_year"] == selected_year)][:3])
    recent_negative = {i["topic"] for i in items if i["kind"] == "lookup"
                       and i.get("lookup_outcome") == "no_qualified_source"
                       and i.get("fiscal_year") == selected_year
                       and datetime.fromisoformat(i["recorded_at"].replace("Z", "+00:00")) >= datetime.now(timezone.utc) - timedelta(hours=24)}
    gaps = []

    def add(key, title, owner, impact, action, reason=None):
        gaps.append(dict(id=key, title=title, owner=owner, impact=impact, action=action, reason=reason))

    market = summary.get("market_context", {})
    public = summary.get("public_data", {})
    wave = wave_support(summary)
    if wave:
        add("wave_prices", "自動波段的價格證據尚未完整", wave["owner"],
            "不能從目前行情自動選用錨點；有來源的人工假設另行審閱。",
            "更新資料" if wave["owner"] == "program" else "查看波段資料資格", wave["status"])
    if market.get("close_status") != "available":
        add("official_price", "官方行情待更新", "program", "先閱讀已取得資料，不能宣稱最新行情。", "更新資料", market.get("close_reason"))
    for dataset, title in (("TaiwanStockPrice", "歷史價量"), ("TaiwanStockPER", "市場估值指標")):
        data = public.get(dataset, {})
        if (not data.get("rows") or data.get("last_update_status") in {"failed", "partial"}
                or data.get("status") in {"quality_warning", "insufficient_data", "stale"}
                or data.get("quality_status") == "quality_warning" or data.get("is_stale")):
            add(dataset, title + "待更新", "program", "保留各自資料日期與品質限制。", "更新資料", data.get("last_update_reason"))
    financial = public.get("TaiwanStockFinancialStatements", {})
    earnings = public.get("VerifiedQuarterlyEarnings")
    if earnings is not None:
        reason = earnings.get("reason")
        if earnings.get("status") != "available":
            engineering = reason in {"source_format_not_supported", "earnings_storage_limit", "source_revision_requires_review",
                                     "earnings_period_requires_refresh", "capital_schedule_format_or_unknown_movement",
                                     "earnings_source_parse_failed", "earnings_source_not_found"}
            engineering = engineering or bool(reason and reason.startswith(("capital_", "note_", "quarter_", "earnings_document_"))
                                               and reason not in {"quarter_missing", "quarter_revision_conflict", "quarter_source_evidence_incomplete"})
            owner = "engineering" if engineering else "program" if (earnings.get("last_update_status") == "failed"
                    or reason in {"not_collected", "earnings_parser_requires_refresh"}) else "assistant"
            coverage = earnings.get("source_coverage") or {}
            impact = "保留逐季數字與日期；不需要填值或核准資料正確性。"
            if reason == "quarter_source_evidence_incomplete":
                owner = "assistant"
                impact = " ".join(coverage.get("blockers", [])) or impact
            add("ttm", "最近四季獲利合計仍有缺項", owner, impact,
                "先閱讀已公布財報" if engineering else "更新資料" if owner == "program" else "交給助理查證", reason)
    elif financial.get("reason") == "share_basis_not_verified":
        add("ttm", "過去一年獲利尚不能可靠合計", "engineering", "財報使用的股數口徑尚未核對，不能由核准代替資料驗證。", "先閱讀已公布財報", financial.get("reason"))
    elif financial.get("status") != "available":
        add("financial", "財報資料待核對", "assistant", "尚不能完整判讀過去獲利；不需要填數字。", "交給助理查證", financial.get("reason"))
    if not matrix:
        for topic, title in (("eps", "全年獲利預估"), ("pe", "同年度估值倍數")):
            matching = [a for a in approved if a["kind"] == topic and a.get("fiscal_year") == selected_year and selected_year]
            drafts = [a for a in assumptions if a["kind"] == topic and not a.get("superseded")
                      and not a.get("approval") and a.get("fiscal_year") == selected_year and selected_year]
            ready = [c for c in candidates if c["topic"] == topic]
            if matching:
                continue
            add(topic, title + ("有資料待審閱" if ready or drafts else "待查證"),
                "user" if ready or drafts else "assistant",
                "尚不能建立完整估值；可以先閱讀或保存部分研究。",
                "閱讀候選與限制" if ready else "檢視待核准草稿" if drafts else "交給助理查證",
                applied.get("reason_code"))
        if not any(g["id"] in {"eps", "pe"} for g in gaps):
            add("valuation_unavailable", "已核准紀錄尚未形成可用情境", "engineering",
                "核准不代表本次可計算；先查程式原因，不重複要求選值。", "查看完整證據", applied.get("reason_code"))
    if summary.get("technical_context", {}).get("status") != "available":
        ready = any(c["topic"] == "anchor" for c in candidates)
        add("anchor", "波段尚待整理與確認", "user" if ready else "assistant",
            "目前沒有可用技術目標，不影響其他研究。", "閱讀波段候選" if ready else "需要波段研究時再查證",
            summary.get("technical_context", {}).get("reason_code"))
    if market.get("market_turnover_status") != "available" or market.get("cbc_status") != "available":
        add("market_context", "市場資金資料尚缺", "program", "目前不能完整判讀市場資金，其他模組仍可閱讀。", "查看來源更新狀態")
    main = next((g for g in gaps if g["owner"] == "user"), None)
    if main is None:
        main = next((g for g in gaps if g["id"] in {"eps", "pe"} and g["id"] not in recent_negative), None)
    if selected_year is None and candidate_years and not matrix:
        main = dict(id="year", owner="user", title="選擇想研究的獲利年度", action="選擇研究年度",
                    impact="不同年度分開比較，選年度不會核准或改寫假設。")
    if main is None:
        main = dict(id="read", owner="user", title="先閱讀，再決定是否保存", action="預覽部分研究",
                    impact="缺項隨研究保留，不必填完所有假設。")
    prompt = (f'請使用 tw-stock-research 與 du-jinlong-research-method 研究 {summary.get("canonical_symbol", "此股票")}，'
              f'研究年度：{selected_year or "尚未選定，需要年度選擇時再詢問"}。先讀本機查證紀錄與有效假設，'
              '依缺項分工查證，保留來源與限制；可自動保留本機查證紀錄，但不要代選數值、核准或保存正式研究。'
              '目前缺項：' + '、'.join(g["title"] for g in gaps))
    if earnings and earnings.get("source_coverage", {}).get("status") == "evidence_incomplete":
        coverage = earnings["source_coverage"]
        prompt += (f'。四季獲利來源上次查核：{coverage["reviewed_at"]}；'
                   + " ".join(coverage["blockers"]) + " " + coverage["next_action"])
    if wave:
        prompt += ('。波段錨點請分清人工來源候選與自動確認轉折；每份候選保留日期、價格口徑、'
                   '角色、來源、採用理由及失效條件。確認時點未提供就明示缺少，不以轉折日期或發布日期代替。'
                   '未還原行情與交易日缺口不能透過使用者核准變成已驗證資料；查無適用來源時保留缺項。')
    return dict(contract_version=GUIDANCE_CONTRACT, selected_year=selected_year,
                available_years=sorted(set(years + candidate_years + ([selected_year] if selected_year else []))), gaps=gaps, next_step=main,
                data_readiness="partial" if any(g["id"] not in {"eps", "pe", "anchor", "valuation_unavailable"} for g in gaps) else "available_with_limits",
                finding="已有程式估值情境可閱讀" if matrix else "估值仍待資料與適用依據",
                candidates=candidates, evidence=evidence.get("items", []), evidence_next_cursor=evidence.get("next_cursor"),
                evidence_versions=[dict(record_id=i["record_id"], content_sha256=i["content_sha256"]) for i in items],
                approved_assumptions=approved, assistant_request=prompt,
                automated_research_running=False, wave_support=wave)
