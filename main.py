import requests
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
LINE_TOKEN   = os.environ.get("LINE_TOKEN", "Mf9B3XhozY5hvnb3FNIAuG2m5tfEAxTjoR3QuJ2tE5EmAFxaqZD29cS/jE3KD0Nial8MDy/YhIzVHNSK+kpIDLsgvcMy4jRsh3sNALP8C0S7gvAjOmObGW1EJXBJQkSaElZhJKic/kDM7epbYBjJXgdB04t89/1O/w1cDnyilFU=")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "Ufdb950a7fa44e578ef5255d8ffc4a3e1")
SYMBOL       = "XAUUSD"
CHECK_EVERY  = 60  # seconds

# ============================================================
# SEND LINE MESSAGE
# ============================================================
def send_line(message):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}]
    }
    try:
        r = requests.post(url, headers=headers, json=body)
        print(f"LINE sent: {r.status_code}")
    except Exception as e:
        print(f"LINE error: {e}")

# ============================================================
# FETCH PRICE DATA (Yahoo Finance - ฟรี)
# ============================================================
def get_candles(symbol="GC=F", interval="5m", period="5d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "interval": interval,
        "range": period,
        "includePrePost": False
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        timestamps = data["chart"]["result"][0]["timestamp"]
        ohlcv = data["chart"]["result"][0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "time":   pd.to_datetime(timestamps, unit="s"),
            "open":   ohlcv["open"],
            "high":   ohlcv["high"],
            "low":    ohlcv["low"],
            "close":  ohlcv["close"],
            "volume": ohlcv["volume"]
        }).dropna()
        return df
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

# ============================================================
# SMC CONDITIONS
# ============================================================

# 1. Strong High / Strong Low (Swing)
def get_strong_levels(df, length=50):
    strong_high = df["high"].rolling(length).max().iloc[-1]
    strong_low  = df["low"].rolling(length).min().iloc[-1]
    return strong_high, strong_low

# 2. Order Block Detection
def get_order_blocks(df, lookback=10):
    bull_ob = None
    bear_ob = None
    close = df["close"].values
    open_ = df["open"].values
    high  = df["high"].values
    low   = df["low"].values

    for i in range(len(df)-lookback, len(df)-1):
        # Bullish OB: bearish candle ก่อน impulse ขึ้น
        if close[i] < open_[i]:
            if close[-1] > close[i]:
                bull_ob = {"high": high[i], "low": low[i], "idx": i}
                break

    for i in range(len(df)-lookback, len(df)-1):
        # Bearish OB: bullish candle ก่อน impulse ลง
        if close[i] > open_[i]:
            if close[-1] < close[i]:
                bear_ob = {"high": high[i], "low": low[i], "idx": i}
                break

    return bull_ob, bear_ob

# 3. Liquidity Swing (Unswept Pivot)
def get_liquidity(df, length=14):
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)

    lq_high = None
    lq_low  = None

    # หา pivot high ล่าสุดที่ยังไม่ถูก sweep
    for i in range(n - length - 1, length, -1):
        if all(highs[i] >= highs[i-length:i]) and all(highs[i] >= highs[i+1:i+length+1]):
            if highs[-1] < highs[i]:  # ยังไม่โดน sweep
                lq_high = highs[i]
                break

    # หา pivot low ล่าสุดที่ยังไม่ถูก sweep
    for i in range(n - length - 1, length, -1):
        if all(lows[i] <= lows[i-length:i]) and all(lows[i] <= lows[i+1:i+length+1]):
            if lows[-1] > lows[i]:  # ยังไม่โดน sweep
                lq_low = lows[i]
                break

    return lq_high, lq_low

# 4. POC (Point of Control) approximation
def get_poc(df, period=50):
    recent = df.tail(period)
    # หา price level ที่มี volume เยอะสุด
    price_levels = np.linspace(recent["low"].min(), recent["high"].max(), 100)
    vol_at_level = []
    for p in price_levels:
        mask = (recent["low"] <= p) & (recent["high"] >= p)
        vol_at_level.append(recent.loc[mask, "volume"].sum())
    poc_price = price_levels[np.argmax(vol_at_level)]
    return poc_price

# 5. Trend Detection (Higher Lows)
def get_trend(df, length=10):
    lows  = df["low"].values
    highs = df["high"].values
    n     = len(df)

    hl_count = 0
    ll_count = 0
    last_low  = lows[-1]
    last_high = highs[-1]

    for i in range(n-2, max(n-length*3, 0), -1):
        if lows[i] < last_low:
            ll_count += 1
            last_low = lows[i]
        elif lows[i] > last_low:
            hl_count += 1
            last_low = lows[i]

    is_uptrend   = hl_count >= 1
    is_downtrend = ll_count >= 1
    return is_uptrend, is_downtrend

# ============================================================
# CHECK ALL CONDITIONS
# ============================================================
def check_signals(df, tol_pct=0.003):
    price  = df["close"].iloc[-1]
    tol    = price * tol_pct

    strong_high, strong_low = get_strong_levels(df)
    bull_ob, bear_ob        = get_order_blocks(df)
    lq_high, lq_low         = get_liquidity(df)
    poc                     = get_poc(df)
    is_uptrend, is_downtrend = get_trend(df)

    buy_signal  = False
    sell_signal = False
    reasons_buy  = []
    reasons_sell = []

    # --- BUY CONDITIONS ---
    if bull_ob:
        ob_near_strong_low = abs(bull_ob["low"] - strong_low) <= tol * 5
        lq_in_zone = lq_low and abs(lq_low - bull_ob["low"]) <= tol * 10
        poc_in_zone = bull_ob["low"] - tol <= poc <= bull_ob["high"] + tol

        if ob_near_strong_low:
            reasons_buy.append(f"✅ OB ({bull_ob['low']:.2f}-{bull_ob['high']:.2f}) @ Strong Low ({strong_low:.2f})")
        if lq_in_zone:
            reasons_buy.append(f"✅ LQ Low intact: {lq_low:.2f}")
        if poc_in_zone:
            reasons_buy.append(f"✅ POC: {poc:.2f} อยู่ใน OB zone")
        if is_uptrend:
            reasons_buy.append(f"✅ Uptrend (Higher Lows)")

        if ob_near_strong_low and lq_in_zone and poc_in_zone and is_uptrend:
            buy_signal = True

    # --- SELL CONDITIONS ---
    if bear_ob:
        ob_near_strong_high = abs(bear_ob["high"] - strong_high) <= tol * 5
        lq_in_zone = lq_high and abs(lq_high - bear_ob["high"]) <= tol * 10
        poc_in_zone = bear_ob["low"] - tol <= poc <= bear_ob["high"] + tol

        if ob_near_strong_high:
            reasons_sell.append(f"✅ OB ({bear_ob['low']:.2f}-{bear_ob['high']:.2f}) @ Strong High ({strong_high:.2f})")
        if lq_in_zone:
            reasons_sell.append(f"✅ LQ High intact: {lq_high:.2f}")
        if poc_in_zone:
            reasons_sell.append(f"✅ POC: {poc:.2f} อยู่ใน OB zone")
        if is_downtrend:
            reasons_sell.append(f"✅ Downtrend (Lower Highs)")

        if ob_near_strong_high and lq_in_zone and poc_in_zone and is_downtrend:
            sell_signal = True

    return buy_signal, sell_signal, reasons_buy, reasons_sell, price

# ============================================================
# MAIN LOOP
# ============================================================
last_signal = None

print("🚀 SMC Alert Bot Started!")
send_line("🚀 SMC Alert Bot เริ่มทำงานแล้ว!\nกำลังเฝ้ากราฟ XAUUSD...")

while True:
    try:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking XAUUSD...")

        df = get_candles("GC=F", "5m", "5d")

        if df is None or len(df) < 100:
            print("Not enough data, retrying...")
            time.sleep(CHECK_EVERY)
            continue

        buy, sell, r_buy, r_sell, price = check_signals(df)

        print(f"Price: {price:.2f} | BUY: {buy} | SELL: {sell}")

        if buy and last_signal != "BUY":
            msg = (
                f"🟢 BUY SIGNAL — XAUUSD\n"
                f"💰 ราคา: {price:.2f}\n"
                f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                + "\n".join(r_buy)
            )
            send_line(msg)
            last_signal = "BUY"
            print("✅ BUY signal sent!")

        elif sell and last_signal != "SELL":
            msg = (
                f"🔴 SELL SIGNAL — XAUUSD\n"
                f"💰 ราคา: {price:.2f}\n"
                f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                + "\n".join(r_sell)
            )
            send_line(msg)
            last_signal = "SELL"
            print("✅ SELL signal sent!")

        elif not buy and not sell:
            last_signal = None

    except Exception as e:
        print(f"Error: {e}")

    time.sleep(CHECK_EVERY)
