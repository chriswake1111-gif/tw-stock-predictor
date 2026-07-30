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
from src.collectors.finmind_collector import FinMindCollector
from src.collectors.cbc_collector import CBCCollector
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.engine.ma_deduction import MADeductionEngine
from src.engine.valuation_eva import ValuationEVAEngine
from src.engine.market_sentiment import MarketSentimentEngine
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
    twse_df = load_ohlcv_data(symbol, db_path)
    if twse_df.empty:
        return {}, pd.DataFrame(), {}, {}

    wave_engine = WaveFibonacciEngine(config_path="config/config.yaml")
    ma_engine = MADeductionEngine(config_path="config/config.yaml")
    val_engine = ValuationEVAEngine(config_path="config/config.yaml")
    sentiment_engine = MarketSentimentEngine(db_path=db_path, config_path="config/config.yaml")

    params = wave_engine.get_symbol_wave_params(symbol)
    targets = wave_engine.calculate_wave_targets(p0=params["p0"], p1=params["p1"], p2=params.get("p2"))
    time_win = wave_engine.check_time_window(twse_df, pivot_date=params.get("pivot_date", "2022-10-25"))

    analyzed_df = ma_engine.calculate_ma_and_deductions(twse_df)
    resonance_series = ma_engine.detect_resonance_signal(analyzed_df)
    analyzed_df["resonance_signal"] = resonance_series

    # 估值 (以台積電為例)
    est_eps = val_engine.estimate_future_eps(historical_ttm_eps=42.0)
    dog_val = val_engine.calculate_dog_master_valuation(eps=est_eps)
    eva_val = val_engine.calculate_eva_floor(nopat=3500.0, invested_capital=15000.0, total_shares_billion=259.0)

    # 熱度
    sentiment = sentiment_engine.check_volume_m1b_overheat(daily_volume_billion=4500.0)

    return targets, analyzed_df, dog_val, eva_val, time_win, sentiment

def create_tu_plotly_chart(df: pd.DataFrame, targets: dict, dog_val: dict, pivot_date: str = "2022-10-25"):
    """杜氏特化 Plotly 互動圖表：K 線 + 均線扣抵標記 + 費氏時間帶 + 估值通道帶"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])

    # 1. K 線圖
    fig.add_trace(
        go.Candlestick(
            x=df['date'],
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name="K 線"
        ),
        row=1, col=1
    )

    # 2. 費氏均線群 (SMA 8, 13, 21, 55, 144)
    colors = {8: "cyan", 13: "orange", 21: "magenta", 55: "green", 144: "blue"}
    for period in [8, 13, 21, 55, 144]:
        if f"SMA_{period}" in df.columns:
            fig.add_trace(
                go.Scatter(x=df['date'], y=df[f"SMA_{period}"], mode='lines', name=f"SMA {period}", line=dict(color=colors.get(period, "gray"), width=1.5)),
                row=1, col=1
            )

    # 3. 均線扣抵未來標記 (Deduction Markers)
    latest_close = df['close'].iloc[-1]
    for period, color in [(8, "cyan"), (21, "magenta"), (55, "green")]:
        if f"deduct_val_{period}" in df.columns:
            deduct_val = df[f"deduct_val_{period}"].iloc[-1]
            slope_up = latest_close > deduct_val
            symbol_shape = "star-triangle-up" if slope_up else "star-triangle-down"
            marker_color = "red" if slope_up else "green"
            
            fig.add_trace(
                go.Scatter(
                    x=[df['date'].iloc[-1]],
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
    cheap_p = dog_val.get("cheap_price", 400.0)
    fair_p = dog_val.get("fair_price", 800.0)
    exp_p = dog_val.get("expensive_price", 1000.0)
    
    fig.add_hrect(y0=0, y1=cheap_p, fillcolor="green", opacity=0.08, line_width=0, annotation_text="便宜價區間", row=1, col=1)
    fig.add_hrect(y0=cheap_p, y1=fair_p, fillcolor="blue", opacity=0.05, line_width=0, annotation_text="合理價區間", row=1, col=1)
    fig.add_hrect(y0=fair_p, y1=exp_p, fillcolor="red", opacity=0.08, line_width=0, annotation_text="昂貴價區間", row=1, col=1)

    # 5. 費波南希時間轉折垂直區間帶 (add_vrect)
    sub_df = df[df['date'] >= pivot_date].reset_index(drop=True)
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
    colors_vol = np.where(df['close'] >= df['open'], 'red', 'green')
    fig.add_trace(
        go.Bar(x=df['date'], y=df['volume'], name="成交量", marker_color=colors_vol),
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
    st.caption("融合「波浪理論」、「費波南希時間/空間」、「均線扣抵共振」、「EVA 估值」與「M1B 籌碼過熱指標」")

    # 側邊欄 Sidebar
    st.sidebar.header("⚙️ 系統設定與控制")
    symbol = st.sidebar.selectbox("請選擇分析標的", ["2330.TW (台積電)", "^TWII (加權指數)"])
    symbol_code = "^TWII" if "^TWII" in symbol else "2330.TW"
    
    if st.sidebar.button("🔄 一鍵強制重新整理數據 (Force Refresh)"):
        st.cache_data.clear()
        st.rerun()

    # 載入數據與引擎計算
    targets, df, dog_val, eva_val, time_win, sentiment = compute_engine_analysis(symbol_code)

    if df.empty:
        st.error("無法載入 K 線數據，請檢查網路或 SQLite 快取")
        return

    latest_row = df.iloc[-1]
    is_resonance = latest_row.get("resonance_signal", False)

    # 頂部 KPI 卡片 Summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("最新收盤價", f"${latest_row['close']:.2f}")
    with col2:
        st.metric("多空共振攻擊訊號", "🔥 亮燈發動" if is_resonance else "⚪ 未觸發 (整理)", delta="強攻訊號" if is_resonance else "中立")
    with col3:
        st.metric("M1B 頭部過熱比率", f"{sentiment.get('volume_m1b_ratio', 0)*100:.2f}%", delta="極端過熱" if sentiment.get('is_overheat') else "正常適中")
    with col4:
        st.metric("費氏時間轉折狀態", f"{time_win.get('elapsed_units', 0)} 天", delta=time_win.get('matching_fib') or "正常發展")

    # 頁籤分流 (Tabs)
    tab1, tab2, tab3 = st.columns(3)
    
    st.markdown("---")
    
    # 呈現 Plotly 杜氏特化 K 線圖
    st.subheader("📊 杜氏特化 Plotly K 線圖 (含均線扣抵預判、費氏時間帶與估值通道)")
    fig = create_tu_plotly_chart(df, targets, dog_val)
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
        st.write(f"**預估未來一年 EPS**: {dog_val.get('estimated_eps')} 元 (雙軌備援模式)")
        st.dataframe(pd.DataFrame([
            {"估值價位類別": "便宜價 (10x PE)", "價格 (元)": dog_val.get("cheap_price")},
            {"估值價位類別": "合理價 (20x PE)", "價格 (元)": dog_val.get("fair_price")},
            {"估值價位類別": "昂貴價 (25x PE)", "價格 (元)": dog_val.get("expensive_price")},
            {"估值價位類別": "EVA 長線價值底盤", "價格 (元)": eva_val.get("eva_floor_price")},
        ]), use_container_width=True)

if __name__ == "__main__":
    main()
