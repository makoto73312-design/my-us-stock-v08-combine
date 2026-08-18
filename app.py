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
st.set_page_config(page_title="🚀 美股量化全方位戰術指揮官 V08", page_icon="🚀", layout="wide")
st.title("🚀 美股量化全方位戰術指揮官 V08 (雙引擎旗艦整合版)")
st.caption("🔥 整合 Seeking Alpha 短線當沖/拉回監控系統 + V07.1 機構級多因子沙盒與 7D 矩陣")

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
# 4. 總經與基本面雷達 (優化 VIX 獨立抓取邏輯)
# ==========================================
@st.cache_data(ttl=1800)
def fetch_us_macro_dataframe():
    try:
        vix_tk = yf.Ticker("^VIX")
        vix_df = vix_tk.history(period="2y")
        
        spy_tk = yf.Ticker("SPY")
        spy_df = spy_tk.history(period="2y")

        if vix_df.empty or spy_df.empty:
            raise ValueError("Yahoo Finance 傳回空數據")

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

        if latest_vix >= 25 or not latest_bull:
            posture_auto = "🥶 極度謹慎型 (大盤空頭/高恐慌)"
        elif latest_vix <= 15 and latest_bull:
            posture_auto = "🚀 大膽進攻型 (晴天多頭行情)"
        else:
            posture_auto = "🛡️ 標準平衡型 (常態橫盤整理)"

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
    
    # 第一引擎指標
    df["EMA10"] = df["Close"].ewm(span=10, adjust=False).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["Vol_SMA20"] = df["Volume"].rolling(window=20).mean()

    # 第二引擎指標
    mf_multiplier = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / high_low_diff
    df['價量動能流'] = (df['Volume'] * mf_multiplier / 1000000).round(2)
    df['CLV'] = (df['Close'] - df['Low']) / high_low_diff
    
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat(
