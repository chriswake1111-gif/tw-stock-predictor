import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.collectors.twse_collector import TWSECollector
from src.analysis_service import RESEARCH_DISCLAIMER, analyze_symbol
from src.strategy.backtester import TuBacktester

# 頁面配置
st.set_page_config(
    page_title="杜金龍台股量化預測與分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# st.cache_data 效能優化快取
@st.cache_data(ttl=3600)
def load_ohlcv_data(symbol: str, db_path: str = "data/cache.db"):
    collector = TWSECollector(db_path=db_path)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365*2)).strftime("%Y-%m-%d")
    return collector.get_ohlcv(symbol, start_date=start_date, end_date=end_date)

@st.cache_data(ttl=3600)
def compute_engine_analysis(symbol: str, db_path: str = "data/cache.db"):
    analysis, analyzed_df = analyze_symbol(symbol, db_path=db_path)
    if analysis.get("status") != "available" or analyzed_df.empty:
        return {}, pd.DataFrame(), {}, {}, {}, {}

    realtime_wave = analysis["wave_analysis"]["realtime_confirmed"]
    return (
        realtime_wave.get("targets", {}),
        analyzed_df,
        analysis["valuation"],
        analysis["eva_valuation"],
        realtime_wave.get("time_window", {}),
        analysis["sentiment"],
    )

def create_tu_plotly_chart(df: pd.DataFrame, targets: dict, dog_val: dict, pivot_date: str | None = None):
    """杜氏特化 Plotly 互動圖表：K 線 + 均線扣抵標記 + 費氏時間帶 + 估值通道帶"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])

    clean_df = df.dropna(subset=['open', 'high', 'low', 'close']).copy()

    # 1. K 線圖
    fig.add_trace(
        go.Candlestick(
            x=clean_df['date'],
            open=clean_df['open'],
            high=clean_df['high'],
            low=clean_df['low'],
            close=clean_df['close'],
            name="K 線"
        ),
        row=1, col=1
    )

    # 2. 費氏均線群 (SMA 8, 13, 21, 55, 144)
    colors = {8: "cyan", 13: "orange", 21: "magenta", 55: "green", 144: "blue"}
    for period in [8, 13, 21, 55, 144]:
        if f"SMA_{period}" in clean_df.columns:
            fig.add_trace(
                go.Scatter(x=clean_df['date'], y=clean_df[f"SMA_{period}"], mode='lines', name=f"SMA {period}", line=dict(color=colors.get(period, "gray"), width=1.5)),
                row=1, col=1
            )

    # 3. 均線扣抵未來標記 (Deduction Markers)
    latest_close = clean_df['close'].iloc[-1]
    for period in [8, 21, 55]:
        if f"deduct_val_{period}" in clean_df.columns:
            deduct_val = clean_df[f"deduct_val_{period}"].iloc[-1]
            if not np.isnan(deduct_val):
                slope_up = latest_close > deduct_val
                symbol_shape = "star-triangle-up" if slope_up else "star-triangle-down"
                marker_color = "red" if slope_up else "green"
                
                fig.add_trace(
                    go.Scatter(
                        x=[clean_df['date'].iloc[-1]],
                        y=[deduct_val],
                        mode='markers+text',
                        marker=dict(symbol=symbol_shape, size=12, color=marker_color),
                        text=[f"扣抵{period:d}: {deduct_val:.1f}"],
                        textposition="top center",
                        name=f"扣抵 {period} 日點"
                    ),
                    row=1, col=1
                )

    # 4. 主人與小狗估值通道帶 (Bands)
    cheap_p = dog_val.get("cheap_price")
    fair_p = dog_val.get("fair_price")
    exp_p = dog_val.get("expensive_price")
    if dog_val.get("status") == "available" and all(
        value is not None for value in [cheap_p, fair_p, exp_p]
    ):
        fig.add_hrect(y0=0, y1=cheap_p, fillcolor="green", opacity=0.08, line_width=0, annotation_text="便宜價區間", row=1, col=1)
        fig.add_hrect(y0=cheap_p, y1=fair_p, fillcolor="blue", opacity=0.05, line_width=0, annotation_text="合理價區間", row=1, col=1)
        fig.add_hrect(y0=fair_p, y1=exp_p, fillcolor="red", opacity=0.08, line_width=0, annotation_text="昂貴價區間", row=1, col=1)

    # 5. 費波南希時間轉折垂直區間帶 (add_vrect)
    if pivot_date:
        sub_df = clean_df[clean_df['date'] >= pivot_date].reset_index(drop=True)
        for fib in [8, 13, 21, 34, 55, 89, 144, 233]:
            if fib < len(sub_df):
                fib_date = sub_df['date'].iloc[fib]
                fig.add_vrect(
                    x0=fib_date, x1=fib_date,
                    fillcolor="purple", opacity=0.3,
                    line=dict(color="purple", width=1.5, dash="dash"),
                    annotation_text=f"Fib {fib}", annotation_position="top left",
                    row=1, col=1
                )

    # 6. 成交量圖 (Volume Subplot)
    colors_vol = np.where(clean_df['close'] >= clean_df['open'], 'red', 'green')
    fig.add_trace(
        go.Bar(x=clean_df['date'], y=clean_df['volume'], name="成交量", marker_color=colors_vol),
        row=2, col=1
    )

    fig.update_layout(
        title="杜金龍特化 K 線圖 (含均線扣抵預判、費氏時間轉折帶與估值通道)",
        xaxis_rangeslider_visible=False,
        height=650,
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return fig

# Streamlit 主介面邏輯
def main():
    st.title("📈 杜金龍台股量化預測與分析系統 (Anti-Gravity TU-System)")
    st.caption("研究／歷史回測／虛擬模擬模式｜不連接券商、不送出真實委託")
    st.info(RESEARCH_DISCLAIMER)

    # 側邊欄 Sidebar
    st.sidebar.header("⚙️ 系統設定與標的選擇")

    preset_options = [
        "2330.TW (台積電)",
        "^TWII (加權指數)",
        "2317.TW (鴻海)",
        "2454.TW (聯發科)",
        "0050.TW (元大台灣50)",
        "2308.TW (台達電)",
        "2603.TW (長榮)",
        "自訂股票代號"
    ]
    
    selected_preset = st.sidebar.selectbox("快速選擇熱門標的", preset_options)
    
    if selected_preset == "自訂股票代號":
        custom_code = st.sidebar.text_input("請輸入任意台股代號 (如 2303, 3008, 2382)", value="2303")
        if not custom_code.endswith(".TW") and not custom_code.endswith(".TWO") and not custom_code.startswith("^"):
            symbol_code = f"{custom_code}.TW"
        else:
            symbol_code = custom_code
    else:
        symbol_code = selected_preset.split(" ")[0]

    if st.sidebar.button("🔄 一鍵強制重新整理數據 (Force Refresh)"):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.info("💡 提示：本系統支援所有上市櫃股票與 ETF，直接輸入股票代號即可由 Local-First 自動抓取與計算分析！")

    # 載入數據與引擎計算
    with st.spinner(f"正在載入與計算 {symbol_code} 數據..."):
        targets, df, dog_val, eva_val, time_win, sentiment = compute_engine_analysis(symbol_code)

    if df.empty:
        st.error(f"無法載入標的 {symbol_code} 的 K 線數據，請確認股票代號是否正確。")
        return

    clean_df = df.dropna(subset=['close'])
    latest_row = clean_df.iloc[-1]
    is_resonance = latest_row.get("resonance_signal", False)

    # 頂部 KPI 卡片 Summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("分析標的 / 最新收盤價", f"{symbol_code}", f"${latest_row['close']:.2f} 元")
    with col2:
        st.metric("多空共振攻擊訊號", "🔥 亮燈發動" if is_resonance else "⚪ 未觸發 (整理)", delta="強攻訊號" if is_resonance else "中立")
    with col3:
        ratio = sentiment.get("turnover_m1b_ratio")
        st.metric("M1B 頭部過熱比率", f"{ratio*100:.2f}%" if ratio is not None else "資料不足", delta="極端過熱" if sentiment.get('is_overheat') else "未觸發／待資料")
    with col4:
        st.metric("費氏時間轉折狀態", f"{time_win.get('elapsed_units', 0)} 天", delta=time_win.get('matching_fib') or "正常發展")

    st.markdown("---")
    
    # 呈現 Plotly 杜氏特化 K 線圖
    st.subheader(f"📊 {symbol_code} 杜氏特化 Plotly K 線圖 (含均線扣抵預判、費氏時間帶與估值通道)")
    fig = create_tu_plotly_chart(clean_df, targets, dog_val, pivot_date=time_win.get("pivot_date"))
    st.plotly_chart(fig, use_container_width=True)

    # 呈現詳細算圖數據
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("🎯 杜波浪價格空間與目標價推導")
        st.write(f"**浪 1 起點 P0**: {targets.get('p0')} | **浪 1 高點 P1**: {targets.get('p1')} | **浪 2 底點 P2**: {targets.get('p2')}")
        st.dataframe(pd.DataFrame([
            {"浪別": "浪 3 (1.382 滿足點)", "推導目標價 (元/點)": targets.get("wave3_1.382")},
            {"浪別": "浪 3 (1.618 主升段)", "推導目標價 (元/點)": targets.get("wave3_1.618")},
            {"浪別": "浪 3 (2.000 擴張段)", "推導目標價 (元/點)": targets.get("wave3_2.000")},
            {"浪別": "浪 3 (2.618 強勢段)", "推導目標價 (元/點)": targets.get("wave3_2.618")},
            {"浪別": "浪 5 (3.236 滿載點)", "推導目標價 (元/點)": targets.get("wave5_3.236")},
        ]), use_container_width=True)

    with col_right:
        st.subheader("🐶 主人與小狗估值模型 & EVA 價值底盤")
        st.write(f"**歷史 TTM EPS**: {dog_val.get('estimated_eps')} 元｜狀態：{dog_val.get('status', 'insufficient_data')}")
        st.dataframe(pd.DataFrame([
            {"估值價位類別": "便宜價 (10x PE)", "價格 (元)": dog_val.get("cheap_price")},
            {"估值價位類別": "合理價 (20x PE)", "價格 (元)": dog_val.get("fair_price")},
            {"估值價位類別": "昂貴價 (25x PE)", "價格 (元)": dog_val.get("expensive_price")},
            {"估值價位類別": "EVA 長線價值底盤", "價格 (元)": eva_val.get("eva_floor_price")},
        ]), use_container_width=True)

if __name__ == "__main__":
    main()
