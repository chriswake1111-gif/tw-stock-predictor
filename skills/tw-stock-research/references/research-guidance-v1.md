# 查證紀錄第一版

僅在本次進行中的研究使用 `evidence-record`。數字、來源與查無結果必須來自實際查證；下列是格式示例，不是投資假設。

```json
{
  "kind": "lookup",
  "topic": "pe",
  "fiscal_year": 2026,
  "title": "尚未找到適用倍數",
  "summary": "已查原發布者與可讀轉述，尚未取得同年度獲利基礎、倍數與理由。",
  "lookup_scope": "2026 全年估值",
  "lookup_outcome": "no_qualified_source",
  "limitations": "歷史報告僅供背景參考"
}
```

`kind` 為 candidate／lookup／brief；`topic` 為 eps／pe／anchor／general。候選另包含：

- `source_url`、`publisher`、`published_date`、`locator`、`reading_scope`。
- `source_access` read／blocked／unread；`source_type` original／attributed_secondary／lead／user_assumption。
- `review_status` reviewable／limited／lead／not_applicable；`unresolved_conflict`。
- `summary` 保留來源說法，`interpretation` 為助理解讀；`basis` 獲利口徑、`limitations`、`recheck_when` 必填完整，未知口徑不虛構。
- 盈餘／倍數有 `fiscal_year`、`value`，`unit` 分別 TWD_per_share／multiple；波段使用 `rule_id` FB-03／FB-04、`anchors`（role、price、market_date）、unit=TWD。
- 僅線索與不適用資料可以缺數值；可供審閱必須已實讀且沒有未解數字衝突。次級來源仍明示轉述限制，不宣稱已讀原始報告。
- `previous_id` 指向同股票、同種類、同主題的最新版本；修訂新建，舊版只讀。
- `brief` 可附 `note_draft` 及 `base_review_fingerprint`（最新 review.financial_content_fingerprint），和正式研究筆記分開。

使用 `evidence-list SYMBOL --history` 閱讀全部版本，依 `next_cursor` 以 `--before` 分頁。自動保留不提供核准／撤銷入口，數值也不進入官方財報、估值輸入或回測。
