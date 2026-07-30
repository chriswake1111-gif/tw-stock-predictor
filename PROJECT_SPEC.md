專案開發規格書：杜金龍台股量化預測與分析系統Project Name: tw-stock-tu-systemVersion: 1.0.0Target Platform: Anti-Gravity / Python 3.10+1. 專案總覽與目標 (Overview)本專案旨在將資深證券分析師杜金龍老師的台股分析體系（融合「波浪理論」、「費波南希時間/空間」、「均線扣抵共振」、「基本面 EPS/EVA 估值」以及「M1B 籌碼過熱指標」）進行量化與程式化實作。系統將透過自動化數據管線（Data Pipeline），每日收集台股個股與大盤交易籌碼數據，結合客觀數學公式推導目標價、時間轉折點與風險預警，最後透過視覺化 Dashboard 與自動化告警模組輸出決策建議。2. 專案目錄架構 (Directory Structure)專案目錄需結構化如下，請 Anti-Gravity 依據此目錄進行模組開發：Plaintexttw-stock-tu-system/
├── docs/                               # 領域知識與研究報告 (使用者已提供)
│   ├── 杜金龍超錢部署分析.md
│   └── 杜金龍台股分析體系與實際趨勢吻合度比對.md
├── config/                             # 系統參數配置檔
│   ├── config.yaml                     # 包含費氏數列、波浪基準點、過熱門檻等設定
│   └── .env.example                    # API 金鑰與資料庫連結設定
├── src/
│   ├── collectors/                     # 數據採集層 (Data Pipeline)
│   │   ├── twse_collector.py           # 證交所/櫃買每日 OHLCV 與融資籌碼
│   │   ├── finmind_collector.py        # 個股財報、EPS、PE/PB、三大法人
│   │   └── cbc_collector.py            # 央行 M1B 貨幣供給數據
│   ├── engine/                         # 杜金龍核心分析引擎
│   │   ├── wave_fibonacci.py           # 波浪目標價與費氏時間轉折計算
│   │   ├── ma_deduction.py             # 均線扣抵與多空共振檢測器
│   │   ├── valuation_eva.py            # EPS/PE 目標價與 EVA 價值底盤計算
│   │   └── market_sentiment.py         # 大盤頭部量 (成交量/M1B) 與融資過熱指標
│   ├── strategy/                       # 交易策略與風控模組
│   │   ├── capital_allocation.py       # 20%/30%/50% 分批進場與箱型網格策略
│   │   └── backtester.py               # Backtrader 回測模組
│   └── ui_alert/                       # 視覺化與告警輸出
│       ├── dashboard.py                # Streamlit 視覺化儀表板
│       └── line_notifier.py            # LINE / Telegram 每日報告推播器
├── tests/                              # 單元測試與歷史數據驗證
│   ├── test_wave_calc.py
│   └── test_ma_deduction.py
├── PROJECT_SPEC.md                     # 本規格書
└── requirements.txt                    # Python 依賴套件
3. 核心技術棧選型 (Technology Stack)數據採集 API：FinMind (台股籌碼與財報)、yfinance (歷史 K 線與快速繪圖)技術分析與指標計算：TA-Lib (底層 C 效能運算)、pandas-ta回測引擎：Backtrader (策略事件驅動回測)數據處理：Pandas, NumPy視覺化儀表板：Streamlit, Plotly任務排程：APScheduler / Cron Job4. 四大核心業務邏輯模組規格 (Core Modules Logic)模組 A：波浪與費氏時空演算模組 (wave_fibonacci.py)價格空間滿足點算圖：給定波段低點（如 $P_0 = 12629$ 點）與浪 1 高點 $P_1$，依據黃金分割比率 $1.382, 1.618, 2.0, 2.618$ 計算第 3 浪主升段與第 5 浪目標價。時間波轉折視窗：帶入費波南希序列（$8, 13, 21, 34, 55, 89, 144$ 天/月），以歷史關鍵低/高點為基準，當前日期達序列視窗時觸發「轉折警戒訊號」。模組 B：均線扣抵與多空共振模組 (ma_deduction.py)費氏多空均線群：計算短天期（SMA 8, 13, 21）與長天期（SMA 55, 144, 233）移動平均線。扣抵值預判邏輯：取得未來 $N$ 日要扣抵的歷史 K 線價格，若當前股價高於扣抵值，判定均線方向向上（多頭支撐）；反之則下彎（空頭壓力）。當短、中、長天期均線同時呈現「扣抵低價且向上發散」時，觸發「多空共振發動」訊號。模組 C：基本面估值與二低一高篩選模組 (valuation_eva.py)主人與小狗估值模型：$\text{合理目標價} = \text{法人預估未來一年 EPS} \times \text{近 5 年歷史平均 PE (如 20~25 倍)}$。EVA 長線價值底盤：根據企業稅後淨利、WACC（資金成本）與投入資本計算經濟附加價值（EVA），推算個股強支撐底盤（例如台積電長線價值底盤）。二低一高選股器：篩選條件：$\text{PE} < \text{市場平均}$ 且 $\text{PB} < 1.5$ 且 $\text{殖利率} > 4\%$，並結合「破底翻」技術型態發出買進訊號。模組 D：總體籌碼與過熱風險控管模組 (market_sentiment.py & capital_allocation.py)大盤頭部天量預警：當單日「上市櫃合計總成交量 / 最新央行 M1B 數據」之比例過高（或單日爆出 2.5 兆以上天量），警示市場陷入極端過熱。槓桿過熱指標：計算全市場融資餘額增幅與大盤漲幅比率，當「融資報酬率 $> 8\%$」時自動觸發減碼訊號。20/30/50 資金建倉與箱型網格：大盤突破共振點建倉 $20\%$，拉回 7%~11%（強勢浪 3）加碼 $30\%$，確認波段底點完成加碼剩餘 $50\%$。5. 分階段開發計畫藍圖 (Roadmap & Phases)Anti-Gravity 需依序完成以下 5 個階段的開發與測試：Phase 1: 基礎設施與數據採集模組 (src/collectors/)實現 twse_collector.py 與 finmind_collector.py。能自動爬取台股每日 OHLCV、融資餘額、三大法人買賣超與個股財報 EPS/PE，並存入本地 SQLite 或 CSV 快取。Phase 2: 技術面與波浪/扣抵計算核心 (src/engine/wave_fibonacci.py, ma_deduction.py)實現費氏均線群（8, 13, 21, 55, 144, 233）計算。撰寫「均线扣抵演算法」與「黃金分割率目標價推算器」，並提供單元測試驗證正確性。Phase 3: 基本面估值與市場熱度模組 (src/engine/valuation_eva.py, market_sentiment.py)實現「目標價 = EPS $\times$ PE」與 EVA 底盤推算邏輯。結合 M1B 與融資餘額，實現「大盤多空過熱溫度計」。Phase 4: 交易策略整合與 Backtrader 歷史回測 (src/strategy/)整合 20-30-50 資金分批配置邏輯與 7%~11% 修正買點規則。使用 Backtrader 對台股歷史大盤（近 10 年）與台積電進行策略回測，產出勝率與 MDD 報告。Phase 5: 視覺化儀表板與自動告警 (src/ui_alert/)使用 Streamlit 打造「台股多空決策 Dashboard」。整合 LINE Bot / Telegram API，於每日 14:30 自動發送當日大盤多空分析與選股清單。