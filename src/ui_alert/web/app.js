/**
 * 杜金龍台股量化預測 - 機構級金融終端 Frontend App
 * 整合 TradingView Lightweight Charts (相容 v3.8 / v4 / v5 API) 與 FastAPI REST API
 */

let chart = null;
let candlestickSeries = null;
let volumeSeries = null;
let maSeriesMap = {};

// 跨版本系列建立輔助函式
function createSeries(chartObj, typeStr, options) {
    if (typeStr === 'Candlestick') {
        if (chartObj.addCandlestickSeries) {
            return chartObj.addCandlestickSeries(options);
        } else if (chartObj.addSeries && LightweightCharts.CandlestickSeries) {
            return chartObj.addSeries(LightweightCharts.CandlestickSeries, options);
        }
    } else if (typeStr === 'Histogram') {
        if (chartObj.addHistogramSeries) {
            return chartObj.addHistogramSeries(options);
        } else if (chartObj.addSeries && LightweightCharts.HistogramSeries) {
            return chartObj.addSeries(LightweightCharts.HistogramSeries, options);
        }
    } else if (typeStr === 'Line') {
        if (chartObj.addLineSeries) {
            return chartObj.addLineSeries(options);
        } else if (chartObj.addSeries && LightweightCharts.LineSeries) {
            return chartObj.addSeries(LightweightCharts.LineSeries, options);
        }
    }
    throw new Error(`無法建立 TradingView 系列: ${typeStr}`);
}

// 初始化 TradingView 圖表
function initTradingViewChart() {
    const container = document.getElementById('chart-container');
    if (!container) return;

    container.innerHTML = ''; // 清空舊容器

    const isDark = document.documentElement.classList.contains('dark');
    
    chart = LightweightCharts.createChart(container, {
        layout: {
            background: { type: 'solid', color: isDark ? '#0f172a' : '#ffffff' },
            textColor: isDark ? '#94a3b8' : '#334155',
        },
        grid: {
            vertLines: { color: isDark ? '#1e293b' : '#f1f5f9' },
            horzLines: { color: isDark ? '#1e293b' : '#f1f5f9' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: isDark ? '#334155' : '#e2e8f0',
        },
        timeScale: {
            borderColor: isDark ? '#334155' : '#e2e8f0',
            timeVisible: true,
            secondsVisible: false,
        },
    });

    // 1. K 線 Candlestick Series
    candlestickSeries = createSeries(chart, 'Candlestick', {
        upColor: '#ef4444',
        downColor: '#22c55e',
        borderUpColor: '#ef4444',
        borderDownColor: '#22c55e',
        wickUpColor: '#ef4444',
        wickDownColor: '#22c55e',
    });

    // 2. 成交量 Volume Series
    volumeSeries = createSeries(chart, 'Histogram', {
        color: '#3b82f6',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        scaleMargins: { top: 0.8, bottom: 0 },
    });

    // 3. 均線系列
    const maColors = { 8: '#22d3ee', 13: '#fb923c', 21: '#e879f9', 55: '#34d399', 144: '#3b82f6' };
    maSeriesMap = {};
    for (const p of [8, 13, 21, 55, 144]) {
        maSeriesMap[p] = createSeries(chart, 'Line', {
            color: maColors[p],
            lineWidth: 1.5,
            title: `SMA${p}`,
        });
    }

    // 自適應視窗大小
    window.addEventListener('resize', () => {
        chart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight
        });
    });
}

// 載入與更新 API 資料
async function fetchAndRenderAnalysis(symbol) {
    try {
        const resp = await fetch(`/api/analysis/${symbol}`);
        if (!resp.ok) {
            alert(`無法獲取標的 ${symbol} 資料`);
            return;
        }

        const data = await resp.json();

        // 1. 更新 KPI 頂部卡片
        document.getElementById('kpi-symbol').innerText = data.symbol;
        document.getElementById('kpi-price').innerText = `$${data.latest_price}`;
        
        const resElem = document.getElementById('kpi-resonance');
        if (data.is_resonance) {
            resElem.className = "text-lg font-bold mt-2 flex items-center space-x-2 text-emerald-500 glow-emerald";
            resElem.innerHTML = `<span class="w-3 h-3 rounded-full bg-emerald-500"></span><span>🔥 亮燈發動中</span>`;
        } else {
            resElem.className = "text-lg font-bold mt-2 flex items-center space-x-2 text-slate-400";
            resElem.innerHTML = `<span class="w-3 h-3 rounded-full bg-slate-400"></span><span>⚪ 未觸發 (整理)</span>`;
        }

        document.getElementById('kpi-m1b').innerText = `${(data.sentiment.volume_m1b_ratio * 100).toFixed(2)}% (${data.sentiment.is_overheat ? '極端過熱' : '正常'})`;
        document.getElementById('kpi-fib-window').innerText = `${data.fib_window.elapsed_units} 個交易日 (${data.fib_window.is_in_window ? '轉折視窗警戒!' : '正常趨勢'})`;

        // 2. 渲染 TradingView K 線與成交量 (time 格式為 YYYY-MM-DD)
        if (data.kline_data && data.kline_data.length > 0) {
            const klineFormatted = data.kline_data.map(item => ({
                time: item.time, // 格式已在後端轉為 YYYY-MM-DD
                open: item.open,
                high: item.high,
                low: item.low,
                close: item.close,
            }));

            const volumeFormatted = data.kline_data.map(item => ({
                time: item.time,
                value: item.volume,
                color: item.close >= item.open ? 'rgba(239, 68, 68, 0.4)' : 'rgba(34, 197, 94, 0.4)'
            }));

            candlestickSeries.setData(klineFormatted);
            volumeSeries.setData(volumeFormatted);

            // 渲染各天期均線 (計算 SMA)
            for (const p of [8, 13, 21, 55, 144]) {
                const maData = [];
                for (let i = p - 1; i < klineFormatted.length; i++) {
                    const slice = klineFormatted.slice(i - p + 1, i + 1);
                    const sum = slice.reduce((acc, curr) => acc + curr.close, 0);
                    maData.push({ time: klineFormatted[i].time, value: sum / p });
                }
                maSeriesMap[p].setData(maData);
            }
        }

        // 3. 畫估值通道參考價位線 (Price Lines)
        if (data.valuation) {
            candlestickSeries.createPriceLine({
                price: data.valuation.cheap_price,
                color: '#22c55e',
                lineWidth: 1.5,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '便宜價 (10x PE)',
            });
            candlestickSeries.createPriceLine({
                price: data.valuation.fair_price,
                color: '#3b82f6',
                lineWidth: 1.5,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '合理價 (20x PE)',
            });
            candlestickSeries.createPriceLine({
                price: data.valuation.expensive_price,
                color: '#ef4444',
                lineWidth: 1.5,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '昂貴價 (25x PE)',
            });
        }

        // 4. 更新波浪目標價表格
        document.getElementById('wave-p0').innerText = data.wave_targets.p0 || '-';
        document.getElementById('wave-p1').innerText = data.wave_targets.p1 || '-';
        document.getElementById('wave-p2').innerText = data.wave_targets.p2 || '-';

        const waveTable = document.getElementById('wave-table-body');
        waveTable.innerHTML = `
            <tr><td class="p-2 font-medium">浪 3 (1.382 滿足點)</td><td class="p-2 text-slate-500">1.382</td><td class="p-2 text-right font-bold text-blue-500">${data.wave_targets['wave3_1.382']}</td></tr>
            <tr><td class="p-2 font-medium">浪 3 (1.618 主升段)</td><td class="p-2 text-slate-500">1.618</td><td class="p-2 text-right font-bold text-emerald-500">${data.wave_targets['wave3_1.618']}</td></tr>
            <tr><td class="p-2 font-medium">浪 3 (2.000 擴張段)</td><td class="p-2 text-slate-500">2.000</td><td class="p-2 text-right font-bold text-amber-500">${data.wave_targets['wave3_2.000']}</td></tr>
            <tr><td class="p-2 font-medium">浪 3 (2.618 強勢段)</td><td class="p-2 text-slate-500">2.618</td><td class="p-2 text-right font-bold text-red-500">${data.wave_targets['wave3_2.618']}</td></tr>
            <tr><td class="p-2 font-medium">浪 5 (3.236 滿載點)</td><td class="p-2 text-slate-500">3.236</td><td class="p-2 text-right font-bold text-purple-500">${data.wave_targets['wave5_3.236']}</td></tr>
        `;

        // 5. 更新估值表格
        document.getElementById('val-eps').innerText = `${data.valuation.estimated_eps} 元`;
        const valTable = document.getElementById('val-table-body');
        valTable.innerHTML = `
            <tr><td class="p-2 font-medium text-emerald-500">便宜價 (10x PE)</td><td class="p-2 text-slate-500">EPS * 10</td><td class="p-2 text-right font-bold">$${data.valuation.cheap_price}</td></tr>
            <tr><td class="p-2 font-medium text-blue-500">合理價 (20x PE)</td><td class="p-2 text-slate-500">EPS * 20</td><td class="p-2 text-right font-bold">$${data.valuation.fair_price}</td></tr>
            <tr><td class="p-2 font-medium text-red-500">昂貴價 (25x PE)</td><td class="p-2 text-slate-500">EPS * 25</td><td class="p-2 text-right font-bold">$${data.valuation.expensive_price}</td></tr>
            <tr><td class="p-2 font-medium text-purple-500">EVA 長線價值底盤</td><td class="p-2 text-slate-500">NOPAT-Capital*WACC</td><td class="p-2 text-right font-bold">$${data.eva_valuation.eva_floor_price}</td></tr>
        `;

    } catch (e) {
        console.error("載入 API 失敗:", e);
    }
}

// 事件處理器綁定
document.addEventListener('DOMContentLoaded', () => {
    initTradingViewChart();
    fetchAndRenderAnalysis('2330');

    // 搜尋按鈕
    const searchBtn = document.getElementById('search-btn');
    const symbolInput = document.getElementById('symbol-input');
    
    const handleSearch = () => {
        const symbol = symbolInput.value.trim();
        if (symbol) {
            initTradingViewChart();
            fetchAndRenderAnalysis(symbol);
        }
    };

    searchBtn.addEventListener('click', handleSearch);
    symbolInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleSearch();
    });

    // 快速選股 Tag 按鈕
    document.querySelectorAll('.quick-symbol').forEach(btn => {
        btn.addEventListener('click', () => {
            const sym = btn.getAttribute('data-symbol');
            symbolInput.value = sym;
            initTradingViewChart();
            fetchAndRenderAnalysis(sym);
        });
    });

    // 深色 / 淺色主題切換
    const themeToggle = document.getElementById('theme-toggle');
    themeToggle.addEventListener('click', () => {
        document.documentElement.classList.toggle('dark');
        const isDark = document.documentElement.classList.contains('dark');
        document.getElementById('theme-icon').innerText = isDark ? '🌙' : '☀️';
        initTradingViewChart();
        fetchAndRenderAnalysis(symbolInput.value || '2330');
    });
});
