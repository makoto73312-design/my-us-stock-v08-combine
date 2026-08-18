import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import requests
import re
import traceback
from datetime import datetime

# ==========================================
# 1. 網頁核心外觀配置
# ==========================================
st.set_page_config(page_title="🚀 美股量化全方位戰術指揮官 V08.1", page_icon="🚀", layout="wide")
st.title("🚀 美股量化全方位戰術指揮官 V08.1 (水晶球二次濾網旗艦版)")
st.caption("🔥 整合 Seeking Alpha 短線飆股監控 + V07.1 機構級沙盒 + **🔮 水晶球高勝率二次濾網**")

# ==========================================
# 2. 雲端自選清單與 Google Form 免權限寫入
# ==========================================
GSHEET_URL = "https://docs.google.com/spreadsheets/d/1491qc1Y59PwCOWaPZblpAieR0_iCI-7KKLtZUuG7Qe4/edit?usp=sharing"
GOOGLE_FORM_ID = "1FAIpQLSdpLHywd-HysLTMbGpuEByQwEaoVaqtvTW0Uwav136m-kIDfQ"
ENTRY_TICKER_ID = "entry.2146824153"
ENTRY_NAME_ID = "entry.1673006020"

@st.cache_data(ttl=60)
def load_tickers_from_gsheet(url):
    try:
        if "docs.google.com" in url:
            csv_url = url.split("/edit")[0] + "/export?format=csv&gid=0" if "/edit" in url else url
        else:
            csv_url = url
            
        df = pd.read_csv(csv_url, header=None)
        raw_list = df.iloc[:, 0].dropna().astype(str).str.strip().str.upper().tolist()
        
        ignore_keywords = ["TICKER", "TICKERS", "STOCK", "STOCKS", "代號", "股票", "SYMBOL", "SYMBOLS", "NAN", "股票代號"]
        tickers = [t for t in raw_list if t and t not in ignore_keywords and not t.startswith("UNNAMED") and not any(c >= '\u4e00' and c <= '\u9fff' for c in t)]
        ticker_str = ", ".join(tickers) if tickers else "NVDA, AAPL, TSLA, MSFT, AMD"
        return ticker_str, tickers
    except Exception:
        return "NVDA, AAPL, TSLA, MSFT, AMD", ["NVDA", "AAPL", "TSLA", "MSFT", "AMD"]

default_ticker_str, default_ticker_list = load_tickers_from_gsheet(GSHEET_URL)

# 側邊欄設定
st.sidebar.header("⚙️ 系統控制台")

with st.sidebar.expander("🌐 雲端自選清單管理與新增", expanded=False):
    st.markdown(f"[🔗 點此開啟 Google 試算表檢視]({GSHEET_URL})")
    st.markdown("---")
    st.markdown("**➕ 免權限寫入新標的至美股雲端**")
    
    with st.form("add_us_stock_form"):
        new_tk_input = st.text_input("美股代號 (如: NVDA)", placeholder="NVDA").strip().upper()
        new_name_input = st.text_input("產業/備註 (選填)", placeholder="AI半導體").strip()
        submit_btn = st.form_submit_button("🚀 一鍵同步寫入美股雲端", use_container_width=True)
        
        if submit_btn:
            if not new_tk_input:
                st.warning("⚠️ 請務必輸入股票代號！")
            else:
                form_url = f"https://docs.google.com/forms/d/e/{GOOGLE_FORM_ID}/formResponse"
                form_data = {ENTRY_TICKER_ID: new_tk_input, ENTRY_NAME_ID: new_name_input}
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                try:
                    res = requests.post(form_url, data=form_data, headers=headers)
                    if res.status_code == 200:
                        st.success(f"🎉 成功寫入【{new_tk_input} - {new_name_input}】！")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"⚠️ 寫入失敗，代碼：[{res.status_code}]")
                except Exception as e:
                    st.error(f"❌ 連線發生錯誤: {e}")

    st.markdown("---")
    if st.button("🔄 強制刷新雲端快取", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

tickers_input = st.sidebar.text_area("📡 當前追蹤股票清單 (以逗號或換行隔開)", default_ticker_str, height=120)
temp_raw_list = [t.strip().upper() for t in re.split(r'[\n\r,\s]+', tickers_input) if t.strip()]
ticker_list = list(dict.fromkeys(temp_raw_list))

backtest_days = st.sidebar.slider("沙盒歷史回測天數", min_value=100, max_value=500, value=300, step=50)
enable_fcf_filter = st.sidebar.checkbox("🛡️ 啟用「FCF 負值」強制攔截", value=True)
enable_crystal_gate = st.sidebar.checkbox("🔮 啟用「水晶球高勝率二次濾網」", value=True, help="僅保留期望值>1.0%、Sharpe>0.3、7D得分>=6且處於壓縮/突破型態的黃金買訊")
show_debug_log = st.sidebar.checkbox("🐛 顯示系統診斷日誌", value=False)

# Session State 初始化
if 'df_results_tab1' not in st.session_state: st.session_state['df_results_tab1'] = pd.DataFrame()
if 'df_results_tab2' not in st.session_state: st.session_state['df_results_tab2'] = pd.DataFrame()
if 'df_results_tab3' not in st.session_state: st.session_state['df_results_tab3'] = pd.DataFrame()
if 'valid_pullbacks' not in st.session_state: st.session_state['valid_pullbacks'] = []
if 'calculated' not in st.session_state: st.session_state.calculated = False
if 'final_df' not in st.session_state: st.session_state.final_df = None
if 'detail_db' not in st.session_state: st.session_state.detail_db = {}
if 'debug_logs' not in st.session_state: st.session_state.debug_logs = []

# ==========================================
# 3. 數據清理與輔助函數
# ==========================================
def clean_and_flatten_df(df):
    if df is None or df.empty: return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        found_level = None
        for level in range(df.columns.nlevels):
            level_vals = [str(c).title() for c in df.columns.get_level_values(level)]
            if 'Close' in level_vals:
                found_level = level
                break
        if found_level is not None: df.columns = df.columns.get_level_values(found_level)
        else: df.columns = df.columns.get_level_values(-1)
            
    standard_map = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume', 'adj close': 'Adj Close'}
    new_cols = [standard_map.get(str(c).lower(), str(c)) for c in df.columns]
    df.columns = new_cols
    return df

def extract_stock_from_chunk(df_chunk, ticker):
    if df_chunk is None or df_chunk.empty: return pd.DataFrame()
    if not isinstance(df_chunk.columns, pd.MultiIndex): return clean_and_flatten_df(df_chunk)
    for lvl in range(df_chunk.columns.nlevels):
        if ticker in df_chunk.columns.get_level_values(lvl):
            try:
                df_sub = df_chunk.xs(ticker, level=lvl, axis=1).copy()
                df_sub = clean_and_flatten_df(df_sub)
                if 'Close' in df_sub.columns and not df_sub.dropna(subset=['Close']).empty:
                    return df_sub.dropna(subset=['Close'])
            except Exception: pass
    return pd.DataFrame()

# ==========================================
# 4. 總經與基本面雷達
# ==========================================
@st.cache_data(ttl=1800)
def fetch_us_macro_dataframe():
    try:
        vix_tk = yf.Ticker("^VIX")
        vix_df = vix_tk.history(period="2y")
        spy_tk = yf.Ticker("SPY")
        spy_df = spy_tk.history(period="2y")

        if vix_df.empty or spy_df.empty: raise ValueError("Yahoo Finance 傳回空數據")

        vix_df = clean_and_flatten_df(vix_df)
        spy_df = clean_and_flatten_df(spy_df)

        vix_c = vix_df[['Close']].rename(columns={'Close': 'VIX'})
        spy_c = spy_df[['Close']].rename(columns={'Close': 'SPY_Close'})

        vix_c.index = pd.to_datetime(pd.to_datetime(vix_c.index).date)
        spy_c.index = pd.to_datetime(pd.to_datetime(spy_c.index).date)

        spy_c['SPY_MA200'] = spy_c['SPY_Close'].rolling(200).mean().fillna(spy_c['SPY_Close'])
        spy_c['Market_Bull'] = spy_c['SPY_Close'] >= spy_c['SPY_MA200']

        df_macro = spy_c.join(vix_c, how='inner').ffill().bfill().dropna()

        latest_vix = float(df_macro['VIX'].iloc[-1])
        latest_bull = bool(df_macro['Market_Bull'].iloc[-1])

        if latest_vix >= 25 or not latest_bull: posture_auto = "🥶 極度謹慎型 (大盤空頭/高恐慌)"
        elif latest_vix <= 15 and latest_bull: posture_auto = "🚀 大膽進攻型 (晴天多頭行情)"
        else: posture_auto = "🛡️ 標準平衡型 (常態橫盤整理)"

        return df_macro, latest_vix, latest_bull, posture_auto, "SUCCESS"

    except Exception as e:
        dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=500, freq='D')
        df_macro = pd.DataFrame({'VIX': 18.0, 'Market_Bull': True, 'SPY_Close': 500.0}, index=dates)
        return df_macro, 18.0, True, "🛡️ 標準平衡型 (預設備援)", f"ERROR: {str(e)}"

df_macro, vix_score, is_spy_bull, market_posture, macro_status = fetch_us_macro_dataframe()

@st.cache_data(ttl=3600)
def fetch_fundamental_info(ticker):
    f_info = {"pe": "-", "fcf": "-", "rev_growth": "-", "fcf_status": "UNKNOWN", "quality_tag": "一般"}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        pe = info.get("trailingPE", None)
        fcf = info.get("freeCashflow", None)
        rev_g = info.get("revenueGrowth", None)
        
        if pe is not None: f_info["pe"] = f"{pe:.1f}倍"
        if fcf is not None:
            f_info["fcf"] = f"${fcf / 1e8:.1f}億"
            f_info["fcf_status"] = "NEGATIVE" if fcf < 0 else "POSITIVE"
        if rev_g is not None: f_info["rev_growth"] = f"{rev_g * 100:+.1f}%"
        if (fcf is not None and fcf > 0) and (rev_g is not None and rev_g > 0.15):
            f_info["quality_tag"] = "🔥 財報雙強"
    except Exception: pass
    return f_info

# ==========================================
# 5. 技術指標計算全集
# ==========================================
def calculate_indicators(df):
    df = clean_and_flatten_df(df)
    high_low_diff = (df['High'] - df['Low']).replace(0, 0.001)
    
    df["EMA10"] = df["Close"].ewm(span=10, adjust=False).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["Vol_SMA20"] = df["Volume"].rolling(window=20).mean()

    mf_multiplier = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / high_low_diff
    df['價量動能流'] = (df['Volume'] * mf_multiplier / 1000000).round(2)
    df['CLV'] = (df['Close'] - df['Low']) / high_low_diff
    
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean().fillna(df['Close'] * 0.03)

    std20 = df['Close'].rolling(20).std().fillna(df['Close'] * 0.02)
    df['MA20'] = df['Close'].rolling(20).mean()
    df['BB_Mid'] = df['MA20']
    df['BB_Upper'] = df['BB_Mid'] + (2.0 * std20)
    df['BB_Lower'] = df['BB_Mid'] - (2.0 * std20)
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid'].replace(0, 1.0)
    df['BB_Squeeze'] = df['BB_Width'] <= df['BB_Width'].rolling(100, min_periods=20).quantile(0.25)

    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA14'] = df['Close'].rolling(14).mean()
    df['50MA'] = df['Close'].rolling(50).mean()
    df['200MA'] = df['Close'].rolling(200).mean()
    df['ROC14'] = df['Close'].pct_change(14)
    
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ema_gain = gain.ewm(com=13, adjust=False).mean()
    ema_loss = loss.ewm(com=13, adjust=False).mean()
    rs = ema_gain / ema_loss.replace(0, 0.001)
    df['RSI_14'] = 100 - (100 / (1 + rs))
    
    df['Vol_MA20'] = df['Vol_SMA20']
    df['動能流_Q80'] = df['價量動能流'].rolling(50).quantile(0.8)
    
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']
    
    macd_shrink = [0] * len(df)
    hist = df['MACD_Hist'].values
    for i in range(1, len(df)):
        if hist[i] < 0 and hist[i] > hist[i-1]: macd_shrink[i] = macd_shrink[i-1] + 1
        else: macd_shrink[i] = 0
    df['MACD_Shrink'] = macd_shrink
    return df

# ==========================================
# 6. 頂部總經抬頭資訊卡
# ==========================================
col_v1, col_v2, col_v3 = st.columns(3)
col_v1.metric("VIX 恐慌指數", f"{vix_score:.2f}", delta="避險戒備" if vix_score >= 25 else "市場平穩", delta_color="inverse")
col_v2.metric("S&P 500 大盤位階", "年線之上 (多頭)" if is_spy_bull else "跌破年線 (空頭)")
col_v3.metric("系統動態總經姿態", market_posture)
st.divider()

# ==========================================
# 7. 主選單 (大分頁系統)
# ==========================================
tab_sa, tab_sandbox_main, tab_7d_bb, tab_verify_sandbox, tab_chart, tab_export = st.tabs([
    "🚀 第一引擎：短線飆股兩階段監控 (SA邏輯)", 
    "📊 第二引擎：沙盒 - 倉位動作與五大策略", 
    "🛡️ 第二引擎：沙盒 - 七維矩陣與布林專家診斷", 
    "⚡ 第二引擎：沙盒 - 五大策略昨日買訊成效",
    "📈 高對比 Plotly K 線與軌跡驗證圖",
    "📥 量化數據匯出中心 (CSV 檔案)"
])

# ==========================================
# Tab 1: 第一引擎 - 美股短線飆股監控系統
# ==========================================
def scan_daily_pullback(tickers):
    watch_list = []
    results_data = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker in enumerate(tickers):
        status_text.text(f"正在掃描日線拉回數據: {ticker} ({i+1}/{len(tickers)})")
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="60d", interval="1d")
            if len(df) < 20: continue
            
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            trend_ok = latest["EMA10"] > latest["EMA20"]
            near_ema10 = (abs(latest["Close"] - latest["EMA10"]) / latest["EMA10"]) <= 0.015
            near_ema20 = (abs(latest["Close"] - latest["EMA20"]) / latest["EMA20"]) <= 0.015
            touch_ema = (latest["Low"] <= latest["EMA10"] and latest["Close"] >= latest["EMA10"]) or \
                        (latest["Low"] <= latest["EMA20"] and latest["Close"] >= latest["EMA20"])
            
            is_pullback = near_ema10 or near_ema20 or touch_ema
            vol_ratio = latest["Volume"] / latest["Vol_SMA20"] if latest["Vol_SMA20"] > 0 else 1.0
            prev_vol_ratio = prev["Volume"] / prev["Vol_SMA20"] if prev["Vol_SMA20"] > 0 else 1.0
            volume_contracted = (vol_ratio <= 0.6) or (prev_vol_ratio <= 0.6)
            
            if trend_ok and is_pullback and volume_contracted:
                watch_list.append(ticker)
            
            results_data.append({
                "股票": ticker, "收盤價": round(latest['Close'], 2),
                "10日線": round(latest['EMA10'], 2), "20日線": round(latest['EMA20'], 2),
                "量能比例": f"{vol_ratio * 100:.1f}%",
                "符合拉回": "🟢 是" if (trend_ok and is_pullback and volume_contracted) else "⚪ 否"
            })
        except Exception: pass
        progress_bar.progress((i + 1) / len(tickers))
        
    status_text.empty()
    progress_bar.empty()
    return watch_list, pd.DataFrame(results_data)

def monitor_intraday_vwap(tickers):
    signals_data = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker in enumerate(tickers):
        status_text.text(f"正在監控盤中 VWAP 數據: {ticker} ({i+1}/{len(tickers)})")
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="5d", interval="15m")
            if df.empty: continue
                
            df["Date"] = df.index.date
            df["Typical_Price"] = (df["High"] + df["Low"] + df["Close"]) / 3
            df["VP"] = df["Typical_Price"] * df["Volume"]
            df["Cum_VP"] = df.groupby("Date")["VP"].cumsum()
            df["Cum_Vol"] = df.groupby("Date")["Volume"].cumsum()
            df["VWAP"] = df["Cum_VP"] / df["Cum_Vol"]
            df["Vol_SMA20"] = df["Volume"].rolling(window=20).mean()
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            crossed_vwap = (prev["Close"] <= prev["VWAP"]) and (latest["Close"] > latest["VWAP"])
            vol_spike = latest["Volume"] >= 1.5 * latest["Vol_SMA20"] if latest["Vol_SMA20"] > 0 else False
            
            if crossed_vwap and vol_spike:
                signals_data.append({
                    "股票": ticker, "狀態": "🚀 觸發買進",
                    "現價": round(latest['Close'], 2), "VWAP": round(latest['VWAP'], 2),
                    "相對量增": f"{latest['Volume'] / latest['Vol_SMA20']:.1f} 倍" if latest['Vol_SMA20'] > 0 else "-",
                    "時間": latest.name.strftime("%H:%M:%S")
                })
        except Exception: pass
        progress_bar.progress((i + 1) / len(tickers))
        
    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(signals_data)

def verify_yesterday_pullback_signals(tickers):
    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker in enumerate(tickers):
        status_text.text(f"正在回測昨日訊號: {ticker} ({i+1}/{len(tickers)})")
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="60d", interval="1d")
            if len(df) < 3: continue
            df = calculate_indicators(df)
            
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            day_before_yesterday = df.iloc[-3]
            
            trend_ok = yesterday["EMA10"] > yesterday["EMA20"]
            near_ema10 = (abs(yesterday["Close"] - yesterday["EMA10"]) / yesterday["EMA10"]) <= 0.015
            near_ema20 = (abs(yesterday["Close"] - yesterday["EMA20"]) / yesterday["EMA20"]) <= 0.015
            touch_ema = (yesterday["Low"] <= yesterday["EMA10"] and yesterday["Close"] >= yesterday["EMA10"]) or \
                        (yesterday["Low"] <= yesterday["EMA20"] and yesterday["Close"] >= yesterday["EMA20"])
            is_pullback = near_ema10 or near_ema20 or touch_ema
            
            vol_ratio = yesterday["Volume"] / yesterday["Vol_SMA20"] if yesterday["Vol_SMA20"] > 0 else 1.0
            prev_vol_ratio = day_before_yesterday["Volume"] / day_before_yesterday["Vol_SMA20"] if day_before_yesterday["Vol_SMA20"] > 0 else 1.0
            volume_contracted = (vol_ratio <= 0.6) or (prev_vol_ratio <= 0.6)
            
            if trend_ok and is_pullback and volume_contracted:
                buy_price = yesterday["Close"]
                today_high = today["High"]
                today_close = today["Close"]
                
                max_profit_pct = ((today_high - buy_price) / buy_price) * 100
                close_profit_pct = ((today_close - buy_price) / buy_price) * 100
                
                status = "🟢 成功發動" if max_profit_pct >= 1.5 else "🟡 橫盤震盪"
                if close_profit_pct < -2.0: status = "🔴 跌破停損"
                
                max_profit_dollar = (max_profit_pct / 100) * 1000
                close_profit_dollar = (close_profit_pct / 100) * 1000
                
                results.append({
                    "股票": ticker, "狀態": status,
                    "昨日收盤 (進場參考)": round(buy_price, 2),
                    "今日最高價": round(today_high, 2), "今日收盤價": round(today_close, 2),
                    "最大潛在獲利(%)": round(max_profit_pct, 2), "收盤帳面損益(%)": round(close_profit_pct, 2),
                    "最大潛在金額($)": round(max_profit_dollar, 2), "收盤帳面金額($)": round(close_profit_dollar, 2)
                })
        except Exception: pass
        progress_bar.progress((i + 1) / len(tickers))
        
    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)

with tab_sa:
    sub_tab1, sub_tab2, sub_tab3 = st.tabs(["📊 第一階段：日線掃描 (盤前)", "🚀 第二階段：盤中監控 (盤中)", "✅ 策略驗證：昨日拉回訊號追蹤"])
    
    with sub_tab1:
        st.header("第一階段：日線拉回量縮掃描")
        st.markdown("建議於**每日美股開盤前**執行，找出『均線多頭、回測 10/20 日均線、且成交量大幅萎縮』的潛在飆股。")
        if st.button("開始執行日線拉回掃描", key="btn_scan_daily"):
            with st.spinner("下載數據並計算中..."):
                valid_pullbacks, df_results = scan_daily_pullback(ticker_list)
                st.session_state['valid_pullbacks'] = valid_pullbacks
                st.session_state['df_results_tab1'] = df_results
                
        if not st.session_state['df_results_tab1'].empty:
            st.success(f"找到 {len(st.session_state['valid_pullbacks'])} 檔符合條件的股票！(已自動帶入盤中監控)")
            st.dataframe(st.session_state['df_results_tab1'], use_container_width=True)

    with sub_tab2:
        st.header("第二階段：盤中 VWAP 監控")
        st.markdown("針對第一階段選出的名單，於**美股開盤期間**監控是否出現『帶量突破 VWAP』的攻擊訊號。")
        if st.button("開始執行盤中 VWAP 監控", key="btn_scan_intraday"):
            if not st.session_state['valid_pullbacks']:
                st.warning("請先至『第一階段』執行日線掃描，或目前沒有符合條件的股票。")
            else:
                with st.spinner("即時分析 15 分鐘線 VWAP 中..."):
                    df_signals = monitor_intraday_vwap(st.session_state['valid_pullbacks'])
                    st.session_state['df_results_tab2'] = df_signals
                    if not df_signals.empty: st.balloons()

        if not st.session_state['df_results_tab2'].empty:
            st.success("🚨 發現買進訊號！")
            st.dataframe(st.session_state['df_results_tab2'], use_container_width=True)

    with sub_tab3:
        st.header("策略驗證：昨日拉回訊號今日表現")
        st.markdown("自動回測清單中的股票：**『如果我昨天在收盤前因為拉回條件買進，今天會賺還是賠？』**")
        if st.button("執行昨日拉回訊號驗證", key="btn_verify_yesterday"):
            with st.spinner("正在回測計算歷史數據..."):
                df_verification = verify_yesterday_pullback_signals(ticker_list)
                st.session_state['df_results_tab3'] = df_verification

        if not st.session_state['df_results_tab3'].empty:
            df_show = st.session_state['df_results_tab3']
            st.success(f"昨日共有 {len(df_show)} 檔股票觸發拉回訊號：")
            st.dataframe(df_show, use_container_width=True)
            
            avg_max_profit_pct = df_show['最大潛在獲利(%)'].mean()
            avg_close_profit_pct = df_show['收盤帳面損益(%)'].mean()
            total_max_dollar = df_show['最大潛在金額($)'].sum()
            total_close_dollar = df_show['收盤帳面金額($)'].sum()
            
            st.markdown("### 📊 總體驗證績效統計")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("最大潛在【平均】獲利", f"{avg_max_profit_pct:.2f}%")
            col2.metric("收盤帳面【平均】損益", f"{avg_close_profit_pct:.2f}%")
            col3.metric("累積潛在總獲利 ($1k/筆)", f"${total_max_dollar:,.2f}")
            col4.metric("累積收盤總損益 ($1k/筆)", f"${total_close_dollar:,.2f}")

# ==========================================
# 沙盒回測引擎核心邏輯
# ==========================================
def run_backtest_engine_v07_us(df_stock, df_macro_input, strategy_name, days, fund_info, fee_rate=0.0005, tax_rate=0.0, slippage=0.001):
    df_st = clean_and_flatten_df(df_stock.copy())
    df_st.index = pd.to_datetime(pd.to_datetime(df_st.index).date)
    
    valid_df = df_st.join(df_macro_input[['VIX', 'Market_Bull', 'SPY_Close']], how='left').ffill().bfill().dropna().tail(days + 1).copy()
    if len(valid_df) < 10:
        return ("⚠️ 數據不足", 0.0, 0.0, 0, "0.00", "D級", "🛑 數據不足", "-", "-", "-", [], [], [], valid_df, 0.0, 0.0, "0.0%", "0.0%", "0.0%", "0.0%", "0.00", "0.0%", "0.0%", "0/7 (無)", [], "常態通道內", 0, {})

    valid_df['Stock_Ret20'] = valid_df['Close'].pct_change(20)
    valid_df['Macro_Ret20'] = valid_df['SPY_Close'].pct_change(20)
    valid_df['RS_20'] = valid_df['Stock_Ret20'] - valid_df['Macro_Ret20']

    capital = 1.0
    equity_curve = [1.0]
    has_position = False
    entry_price, entry_price_with_cost, current_stop_price, highest_price_prior = 0.0, 0.0, 0.0, 0.0
    pos_min_low, pos_max_high = 0.0, 0.0
    mae_list, mfe_list, win_returns, loss_returns, all_returns = [], [], [], [], []
    trade_logs, plot_buys, plot_sells = [], [], []

    dates = valid_df.index
    opens, highs, lows, closes = valid_df['Open'].values, valid_df['High'].values, valid_df['Low'].values, valid_df['Close'].values
    vixs, m_bulls = valid_df['VIX'].values, valid_df['Market_Bull'].values

    s_ma_vals = valid_df['MA5'].values if "A:" in strategy_name else (valid_df['MA14'].values if "B:" in strategy_name else valid_df['MA20'].values)
    m50_vals, m200_vals = valid_df['50MA'].values, valid_df['200MA'].values
    r14_vals, rsi_vals = valid_df['ROC14'].values, valid_df['RSI_14'].values
    vol_vals, vol_m20_vals = valid_df['Volume'].values, valid_df['Vol_MA20'].values
    m_shrink_vals, m_hist_vals = valid_df['MACD_Shrink'].values, valid_df['MACD_Hist'].values
    clv_vals, atr_vals = valid_df['CLV'].values, valid_df['ATR14'].values
    bb_mid_vals, bb_upper_vals, bb_lower_vals, bb_sqz_vals = valid_df['BB_Mid'].values, valid_df['BB_Upper'].values, valid_df['BB_Lower'].values, valid_df['BB_Squeeze'].values
    pv_flow_vals, q80_vals = valid_df['價量動能流'].values, valid_df['動能流_Q80'].values
    rs_vals = valid_df['RS_20'].values

    pending_buy_signal = False
    signal_yesterday_triggered = False
    last_exit_was_today = False

    for i in range(1, len(valid_df)):
        date_str = dates[i].strftime('%Y-%m-%d')
        open_p, high_p, low_p, close_p = opens[i], highs[i], lows[i], closes[i]
        
        vix_y, bull_y = vixs[i-1], m_bulls[i-1]
        if vix_y >= 25 or not bull_y: rsi_max, vol_mult, dip_pct = 65, 1.50, -0.15
        elif vix_y <= 15 and bull_y: rsi_max, vol_mult, dip_pct = 75, 1.05, -0.08
        else: rsi_max, vol_mult, dip_pct = 70, 1.20, -0.10

        atr_p = atr_vals[i-1]
        atr_multiplier = 2.0 if "C:" in strategy_name else 1.5

        if not has_position:
            if pending_buy_signal:
                has_position = True
                pending_buy_signal = False
                entry_price = open_p * (1 + slippage)
                entry_price_with_cost = entry_price * (1 + fee_rate)
                highest_price_prior = open_p
                current_stop_price = entry_price - (atr_multiplier * atr_p)
                pos_min_low, pos_max_high = low_p, high_p
                trade_logs.append({"交易日期": date_str, "動作狀態": "🟢 買入進場 (BUY)", "執行價格": f"${entry_price:.2f}", "單筆報酬": "-"})
                plot_buys.append((dates[i], entry_price))
        else:
            pos_min_low = min(pos_min_low, low_p)
            pos_max_high = max(pos_max_high, high_p)
            new_trailing_stop = highest_price_prior - (atr_multiplier * atr_p)
            current_stop_price = max(current_stop_price, new_trailing_stop)
            
            is_exit = False
            if low_p <= current_stop_price:
                is_exit = True
                exit_price = min(open_p, current_stop_price) * (1 - slippage)

            if is_exit:
                exit_price_after_cost = exit_price * (1 - fee_rate - tax_rate)
                trade_return = (exit_price_after_cost - entry_price_with_cost) / entry_price_with_cost
                capital *= (1 + trade_return)
                all_returns.append(trade_return)

                mae_list.append((pos_min_low - entry_price) / entry_price)
                mfe_list.append((pos_max_high - entry_price) / entry_price)

                if trade_return > 0: win_returns.append(trade_return)
                else: loss_returns.append(abs(trade_return))

                has_position = False
                if i == len(valid_df) - 1: last_exit_was_today = True
                trade_logs.append({"交易日期": date_str, "動作狀態": "🔴 賣出出場 (SELL)", "執行價格": f"${exit_price:.2f}", "單筆報酬": f"{trade_return*100:+.2f}%"})
                plot_sells.append((dates[i], exit_price))

        equity_curve.append(capital)

        if has_position:
            highest_price_prior = max(highest_price_prior, high_p)
        else:
            c_p, sma_p, m50_p, m200_p = closes[i], s_ma_vals[i], m50_vals[i], m200_vals[i]
            r14_p, rsi_p, clv_p = r14_vals[i], rsi_vals[i], clv_vals[i]
            vol_p, vol_m20_p = vol_vals[i], vol_m20_vals[i]
            m_shrink_p, m_hist_p = m_shrink_vals[i], m_hist_vals[i]
            m_hist_y = m_hist_vals[i-1]
            pv_flow_p, q80_p, rs_p = pv_flow_vals[i], q80_vals[i], rs_vals[i]
            bb_upper_p, bb_sqz_y = bb_upper_vals[i], bb_sqz_vals[i-1]
            m50_y = m50_vals[i-3] if i >= 3 else m50_p

            if "A:" in strategy_name:
                if (m_shrink_p >= 1 or (m_hist_p > m_hist_y and m_hist_p > 0)) and r14_p > 0 and rsi_p < rsi_max: pending_buy_signal = True
            elif "B:" in strategy_name:
                if c_p > sma_p and vol_p > vol_m20_p * vol_mult and clv_p >= 0.65 and rs_p > 0 and (bb_sqz_y or c_p >= bb_upper_p * 0.98): pending_buy_signal = True
            elif "C:" in strategy_name:
                if c_p > bb_upper_p and vol_p > vol_m20_p * (vol_mult * 1.1) and clv_p >= 0.70 and rs_p > 0.02: pending_buy_signal = True
            elif "D:" in strategy_name:
                if m200_p > 0 and (c_p - m200_p)/m200_p <= dip_pct and rsi_p < 35 and m_shrink_p >= 1 and c_p > opens[i]: pending_buy_signal = True
            elif "E:" in strategy_name:
                if pv_flow_p > q80_p and pv_flow_p > 0 and c_p > m50_p and m50_p >= m50_y and rs_p > 0: pending_buy_signal = True

            if i == len(valid_df) - 2 and pending_buy_signal: signal_yesterday_triggered = True

    today_open_p, today_close_p = opens[-1], closes[-1]
    today_intraday_ret = (today_close_p - today_open_p) / today_open_p if today_open_p > 0 else 0.0
    yesterday_verification = {
        "yesterday_buy": signal_yesterday_triggered, "today_open": f"${today_open_p:.2f}", "today_close": f"${today_close_p:.2f}",
        "today_ret_pct": f"{today_intraday_ret * 100:+.2f}%", "raw_today_ret": today_intraday_ret, "is_win": "🟢 獲利" if today_intraday_ret > 0 else "🔴 虧損"
    }

    latest_idx = -1
    d1_bull = bool(m_bulls[latest_idx])
    d2_vix = bool(vixs[latest_idx] < 22.0)
    d3_rsi = bool(45.0 <= rsi_vals[latest_idx] <= 75.0)
    d4_vol = bool(vol_vals[latest_idx] > vol_m20_vals[latest_idx])
    d5_macd = bool(m_hist_vals[latest_idx] > 0 or m_shrink_vals[latest_idx] >= 1)
    d6_fcf = bool(fund_info.get("fcf_status") != "NEGATIVE")
    d7_rs = bool(rs_vals[latest_idx] > 0.0)

    matrix_7d_details = [
        {"戰術維度項目": "1. 大盤位階 (200MA)", "檢核標準": "指數高於年線 (多頭市場)", "當前狀態": "✅ 符合" if d1_bull else "❌ 未達標"},
        {"戰術維度項目": "2. VIX 恐慌位階", "檢核標準": "恐慌指數 < 22 (低風險)", "當前狀態": "✅ 符合" if d2_vix else "❌ 高恐慌"},
        {"戰術維度項目": "3. RSI 區間動能", "檢核標準": "14日 RSI 介於 45~75 (健康升勢)", "當前狀態": "✅ 符合" if d3_rsi else "❌ 過熱/過冷"},
        {"戰術維度項目": "4. 攻擊量能發動", "檢核標準": "當日成交量 > 20日均量", "當前狀態": "✅ 符合" if d4_vol else "❌ 量能平淡"},
        {"戰術維度項目": "5. MACD 柱狀動能", "檢核標準": "MACD 柱狀體翻紅或綠柱連續收斂", "當前狀態": "✅ 符合" if d5_macd else "❌ 柱體弱化"},
        {"戰術維度項目": "6. 自由現金流 FCF", "檢核標準": "近四季 FCF >= 0 (營運健全)", "當前狀態": "✅ 符合" if d6_fcf else "❌ 現金流赤字"},
        {"戰術維度項目": "7. 相對強弱 RS20", "檢核標準": "近 20 日漲幅跑贏大盤 Alpha > 0", "當前狀態": "✅ 符合" if d7_rs else "❌ 跑輸大盤"}
    ]

    score_7d = sum([d1_bull, d2_vix, d3_rsi, d4_vol, d5_macd, d6_fcf, d7_rs])
    tag_7d = "極強" if score_7d >= 6 else ("強勢" if score_7d >= 4 else ("中性" if score_7d >= 3 else "偏弱"))
    matrix_7d_str = f"{score_7d}/7 ({tag_7d})"

    last_c, last_l = closes[latest_idx], lows[latest_idx]
    last_mid, last_up, last_low = bb_mid_vals[latest_idx], bb_upper_vals[latest_idx], bb_lower_vals[latest_idx]
    last_sqz = bb_sqz_vals[latest_idx]

    if last_sqz: bb_status_str = "🔥 帶狀極致壓縮 (準備發動)"
    elif last_c >= last_up: bb_status_str = "🚀 突破布林上軌 (強勢多頭)"
    elif last_l <= last_low: bb_status_str = "💎 觸及布林下軌 (超賣回歸)"
    elif last_c < last_mid: bb_status_str = "⚠️ 跌破 20MA 中軌 (離場防守)"
    elif abs(last_c - last_mid) / last_mid <= 0.015: bb_status_str = "🛡️ 貼近 20MA 中軌 (回檔支撐)"
    else: bb_status_str = "⚖️ 常態通道內整理"

    total_trades = len(all_returns)
    win_trades = len(win_returns)
    total_return = capital - 1.0
    win_rate = win_trades / total_trades if total_trades > 0 else 0.0
    avg_win = np.mean(win_returns) if win_trades > 0 else 0.0
    avg_loss = np.mean(loss_returns) if (total_trades - win_trades) > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    
    gross_profit = np.sum(win_returns)
    gross_loss = np.sum(loss_returns)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)
    pf_str = "無限" if profit_factor == 99.9 else f"{profit_factor:.2f}"

    years = max(len(valid_df) / 252.0, 0.1)
    cagr = (capital ** (1.0 / years)) - 1.0 if capital > 0 else -1.0

    eq_arr = np.array(equity_curve)
    peaks = np.maximum.accumulate(eq_arr)
    drawdowns = (eq_arr - peaks) / peaks
    mdd = np.min(drawdowns) if len(drawdowns) > 0 else 0.0

    daily_returns = pd.Series(eq_arr).pct_change().dropna()
    std_ret = daily_returns.std()
    sharpe = (daily_returns.mean() / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

    avg_mae = np.mean(mae_list) if len(mae_list) > 0 else 0.0
    avg_mfe = np.mean(mfe_list) if len(mfe_list) > 0 else 0.0

    if expectancy > 0.02 and sharpe > 1.0 and abs(mdd) < 0.15: grade = "S級 (極優)"
    elif expectancy > 0.01 and sharpe > 0.5: grade = "A級 (優良)"
    elif expectancy > 0.0 and total_return > 0: grade = "B級 (標準)"
    elif total_return > -0.05: grade = "C級 (平庸)"
    else: grade = "D級 (劣等)"

    current_close = closes[-1]
    if has_position:
        current_status = "📦 獲利續抱中 (HOLD)"
        unrealized_pnl = (current_close - entry_price_with_cost) / entry_price_with_cost
        pnl_str = f"{unrealized_pnl*100:+.2f}%"
        entry_price_str = f"${entry_price:.2f}"
        sl_price_str = f"${current_stop_price:.2f}"
    elif pending_buy_signal:
        current_status = "🟢 買入訊號/新進場 (BUY)"
        entry_price_str = f"${current_close:.2f}"
        sl_price_str = f"${current_close - (atr_multiplier * atr_vals[-1]):.2f}"
        pnl_str = "0.00%"
    elif last_exit_was_today:
        current_status, entry_price_str, sl_price_str, pnl_str = "🔴 觸發防守賣出 (SELL)", "-", "-", "-"
    else:
        current_status, entry_price_str, sl_price_str, pnl_str = "💵 空手觀望 (CASH)", "-", "-", "-"

    if enable_fcf_filter and fund_info["fcf_status"] == "NEGATIVE" and ("HOLD" in current_status or "BUY" in current_status):
        current_status = "⚠️ 現金流赤字/風控阻擋 (CASH)"

    # 🔮 水晶球黃金濾網判斷邏輯
    pass_crystal_gate = (
        expectancy > 0.01 and 
        sharpe > 0.30 and 
        score_7d >= 6 and 
        ("極致壓縮" in bb_status_str or "突破布林上軌" in bb_status_str)
    )
    crystal_tag = "🔮 黃金買訊 (Pass)" if pass_crystal_gate else "⚪ 一般/過濾 (Filter)"

    latest_rs = rs_vals[-1] * 100 if len(rs_vals) > 0 else 0.0
    rs_tag = f"{latest_rs:+.1f}%"

    return ("📡 運算完畢", total_return, win_rate, total_trades, pf_str, grade, 
            current_status, entry_price_str, sl_price_str, pnl_str, trade_logs, 
            plot_buys, plot_sells, valid_df, entry_price, current_stop_price, rs_tag,
            f"{expectancy*100:+.2f}%", f"{cagr*100:+.1f}%", f"{mdd*100:.1f}%", 
            f"{sharpe:.2f}", f"{avg_mae*100:.1f}%", f"{avg_mfe*100:+.1f}%", 
            matrix_7d_str, matrix_7d_details, bb_status_str, score_7d, yesterday_verification, crystal_tag)

def process_single_stock_us(ticker, df_stock, backtest_days, df_macro_data, strategies):
    try:
        if df_stock is None or df_stock.empty or len(df_stock) < 10:
            stock_reports = []
            for strat in strategies:
                stock_reports.append({
                    "股票代號": ticker, "當前市價": "-", "策略手法": strat, "倉位狀態": "🛑 數據不足",
                    "期望值 Expectancy": "0.0%", "七維戰術矩陣": "0/7 (無)", "布林通道位階": "數據不足",
                    "綜合評級": "D級", "大盤 Alpha (RS20)": "0.0%", "年化 CAGR": "0.0%", 
                    "最大回撤 MDD": "0.0%", "夏普比率 Sharpe": "0.00", "平均浮虧 MAE": "0.0%", 
                    "平均浮盈 MFE": "0.0%", "建議進場價": "-", "未實現損益": "-", "ATR動態防守價": "-",
                    "複利總報酬": "0.0%", "歷史勝率": "0.0%", "交易次數": 0, "獲利因子": "0.00", "7D得分": 0, "昨日買訊": False, "水晶球黃金濾網": "⚪ 一般/過濾 (Filter)"
                })
            return stock_reports, {}, f"❌ [{ticker}] 找不到 K 線數據"

        df_stock = clean_and_flatten_df(df_stock)
        df_stock = calculate_indicators(df_stock)
        
        df_temp_clean = df_stock.dropna(subset=['Close'])
        current_close = float(df_temp_clean['Close'].iloc[-1]) if not df_temp_clean.empty else 0.0
        fund_info = fetch_fundamental_info(ticker)

        stock_reports, stock_details = [], {}
        
        for strat in strategies:
            (radar, ret, win, trades, pf, grade, cur_status, entry_price_val, 
             sl_price, pnl, t_logs, p_buys, p_sells, v_df, raw_entry, raw_sl, 
             rs_tag, expectancy_str, cagr_str, mdd_str, sharpe_str, mae_str, mfe_str, 
             matrix_7d_str, matrix_7d_details, bb_status_str, score_7d_num, yest_ver, crystal_tag) = run_backtest_engine_v07_us(
                df_stock, df_macro_data, strat, backtest_days, fund_info
            )
            
            stock_details[(ticker, strat)] = {
                "logs": pd.DataFrame(t_logs), "buys": p_buys, "sells": p_sells, 
                "v_df": v_df, "matrix_7d_details": matrix_7d_details, "matrix_7d_str": matrix_7d_str,
                "yest_ver": yest_ver
            }

            stock_reports.append({
                "股票代號": ticker, "當前市價": f"${current_close:.2f}", "策略手法": strat,
                "倉位狀態": cur_status, "水晶球黃金濾網": crystal_tag, "期望值 Expectancy": expectancy_str,
                "七維戰術矩陣": matrix_7d_str, "布林通道位階": bb_status_str, "綜合評級": grade, "大盤 Alpha (RS20)": rs_tag, 
                "年化 CAGR": cagr_str, "最大回撤 MDD": mdd_str, "夏普比率 Sharpe": sharpe_str, 
                "平均浮虧 MAE": mae_str, "平均浮盈 MFE": mfe_str, "建議進場價": entry_price_val, 
                "未實現損益": pnl, "ATR動態防守價": sl_price, "複利總報酬": f"{ret * 100:+.2f}%", 
                "歷史勝率": f"{win * 100:.1f}%", "交易次數": trades, "獲利因子": pf, "7D得分": score_7d_num,
                "昨日買訊": yest_ver.get("yesterday_buy", False), "今日開盤": yest_ver.get("today_open", "-"),
                "今日收盤": yest_ver.get("today_close", "-"), "今日實質漲跌": yest_ver.get("today_ret_pct", "-"),
                "當日驗證": yest_ver.get("is_win", "-")
            })
        return stock_reports, stock_details, "SUCCESS"
    except Exception as e:
        err_detail = f"💥 [{ticker}] 運算例外: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        stock_reports = []
        for strat in strategies:
            stock_reports.append({
                "股票代號": ticker, "當前市價": "-", "策略手法": strat, "倉位狀態": "🛑 數據不足",
                "期望值 Expectancy": "0.0%", "七維戰術矩陣": "0/7 (無)", "布林通道位階": "數據不足",
                "綜合評級": "D級", "大盤 Alpha (RS20)": "0.0%", "年化 CAGR": "0.0%", "最大回撤 MDD": "0.0%",
                "夏普比率 Sharpe": "0.00", "平均浮虧 MAE": "0.0%", "平均浮盈 MFE": "0.0%", "建議進場價": "-",
                "未實現損益": "-", "ATR動態防守價": "-", "複利總報酬": "0.0%", "歷史勝率": "0.0%", "交易次數": 0, "獲利因子": "0.00", "7D得分": 0, "昨日買訊": False, "水晶球黃金濾網": "⚪ 一般/過濾 (Filter)"
            })
        return stock_reports, {}, err_detail

# ==========================================
# 第二引擎：沙盒執行控制器
# ==========================================
if st.sidebar.button("🚀 啟動沙盒全自動多因子掃描引擎", use_container_width=True):
    st.session_state.debug_logs = []
    logs = st.session_state.debug_logs
    logs.append(f"🟢 [1] 解析股票代號清單 (共 {len(ticker_list)} 檔)...")
    logs.append(f"🟢 [2] 總經環境狀態: {macro_status}")

    chunk_size = 20
    ticker_chunks = [ticker_list[i:i + chunk_size] for i in range(0, len(ticker_list), chunk_size)]
    master_report = []
    strategies = ["A: 激進動能型", "B: 穩健波段型", "C: 槓桿強勢型", "D: 均值回歸抄底型", "E: 價量動能流跟隨型"]

    for idx_chunk, chunk in enumerate(ticker_chunks):
        logs.append(f"📡 正在下載第 {idx_chunk+1} 批數據 ({chunk[:3]}...)")
        try:
            df_chunk = yf.download(chunk, period="2y", progress=False, threads=True)
        except Exception as e:
            df_chunk = pd.DataFrame()

        for ticker in chunk:
            df_single = extract_stock_from_chunk(df_chunk, ticker)
            s_reports, s_details, err_status = process_single_stock_us(ticker, df_single, backtest_days, df_macro, strategies)
            if s_reports:
                master_report.extend(s_reports)
                st.session_state.detail_db.update(s_details)

    st.session_state.final_df = pd.DataFrame(master_report)
    st.session_state.calculated = True
    st.session_state["scan_time_us"] = datetime.now().strftime("%H:%M:%S")

if show_debug_log and st.session_state.get("debug_logs"):
    with st.sidebar.expander("🐛 系統診斷日誌", expanded=True):
        st.code("\n".join(st.session_state.debug_logs), language="text")

# ==========================================
# Tab 2: 沙盒 - 倉位動作與五大策略
# ==========================================
with tab_sandbox_main:
    if st.session_state.calculated and not st.session_state.final_df.empty:
        st.caption(f"✅ 上次掃描成功時間：{st.session_state.get('scan_time_us', '')}")
        st.markdown("### 🎯 **倉位狀態分類面板**")
        
        status_tabs = st.tabs(["🟢 新進場 / 買入訊號 (BUY)", "📦 獲利續抱中 (HOLD)", "🔴 觸發防守賣出 (SELL)", "💵 空手觀望 / 風控阻擋"])
        df_all = st.session_state.final_df.copy()

        # 若使用者開啟水晶球濾網，自動過濾為黃金強勢標的
        if enable_crystal_gate:
            df_display = df_all[df_all['水晶球黃金濾網'].str.contains("Pass")].copy()
            st.info("🔮 **已自動套用「水晶球勝率二次濾網」**（僅展示期望值>1%、Sharpe>0.3、7D>=6 且處於壓縮/突破強勢型態之標的）")
        else:
            df_display = df_all

        with status_tabs[0]:
            df_buy = df_display[df_display['倉位狀態'].str.contains("BUY|買入|新進場", na=False)].copy()
            st.metric("🟢 當前新進場標的總數", f"{len(df_buy)} 筆")
            st.dataframe(df_buy, use_container_width=True, hide_index=True)

        with status_tabs[1]:
            df_hold = df_display[df_display['倉位狀態'].str.contains("HOLD|續抱", na=False)].copy()
            st.metric("📦 當前獲利續抱標的總數", f"{len(df_hold)} 筆")
            st.dataframe(df_hold, use_container_width=True, hide_index=True)

        with status_tabs[2]:
            df_sell = df_display[df_display['倉位狀態'].str.contains("SELL|賣出|防守", na=False)].copy()
            st.metric("🔴 當前防守離場標的總數", f"{len(df_sell)} 筆")
            st.dataframe(df_sell, use_container_width=True, hide_index=True)

        with status_tabs[3]:
            df_cash = df_display[df_display['倉位狀態'].str.contains("CASH|觀望|赤字|風控", na=False)].copy()
            st.metric("💵 當前觀望標的總數", f"{len(df_cash)} 筆")
            st.dataframe(df_cash, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("### 📋 **
