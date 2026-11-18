

import os
import time
import requests

from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

BASE_URL = "https://biquote.io/api"
CACHE = {}
CACHE_SECONDS = 50


def normalize_symbol(symbol):
    symbol = symbol.upper().replace("_OTC", "").replace("/", "")
    if len(symbol) != 6 or not symbol.isalpha():
        raise ValueError("Use a valid Forex pair, e.g. EURUSD")
    return symbol


def normalize_interval(tf):
    mapping = {
        "60": "1m",
        "1": "1m",
        "1m": "1m",
        "300": "5m",
        "5m": "5m",
        "900": "15m",
        "15m": "15m",
        "3600": "1h",
        "1h": "1h"
    }
    return mapping.get(str(tf), "1m")


def fetch_candles(symbol="EURUSD", interval="1m", limit=100):
    symbol = normalize_symbol(symbol)
    interval = normalize_interval(interval)
    limit = max(35, min(int(limit), 100))

    key = f"{symbol}:{interval}:{limit}"
    now = time.time()

    if key in CACHE:
        saved_time, saved_data = CACHE[key]
        if now - saved_time < CACHE_SECONDS:
            return saved_data, True

    url = f"{BASE_URL}/{symbol}/ohlc"

    response = requests.get(
        url,
        params={"interval": interval, "limit": limit},
        timeout=20
    )
    response.raise_for_status()

    result = response.json()
    bars = result.get("bars", [])

    if not isinstance(bars, list) or not bars:
        raise ValueError("No candle data returned by provider")

    candles = []

    for bar in bars:
        try:
            dt = datetime.fromisoformat(
                bar["openTime"].replace("Z", "+00:00")
            )

            candles.append({
                "time": int(dt.timestamp()),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("tickVolume", 0)),
                "isOpen": bool(bar.get("isOpen", False))
            })
        except (KeyError, TypeError, ValueError):
            continue

    # Provider returns newest first.
    # Indicators and charts use oldest first.
    candles.sort(key=lambda x: x["time"])

    if len(candles) < 35:
        raise ValueError("Not enough valid candles")

    data = {
        "symbol": symbol,
        "interval": interval,
        "timeframe": 60 if interval == "1m" else None,
        "candles": candles,
        "bars": candles,
        "data": candles,
        "count": len(candles),
        "provider": "Biquote",
        "isOpen": candles[-1]["isOpen"],
        "otc": False
    }

    CACHE[key] = (now, data)
    return data, False


def calculate_ema(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    result = [current]

    for value in values[period:]:
        current = (
            (value - current) * multiplier + current
        )
        result.append(current)

    return result


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def analyze(candles):
    closes = [float(c["close"]) for c in candles]

    if len(closes) < 35:
        return {
            "signal": "NO TRADE",
            "confidence": 0,
            "error": "At least 35 candles are required"
        }

    rsi = calculate_rsi(closes)

    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)

    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)

    if not all([rsi is not None, ema9, ema21, ema12, ema26]):
        return {"signal": "NO TRADE", "confidence": 0}

    # Align EMA12 and EMA26 to the same candle positions.
    macd_line = [
        fast - slow
        for fast, slow in zip(ema12[-len(ema26):], ema26)
    ]

    macd_signal = calculate_ema(macd_line, 9)

    if not macd_signal:
        return {"signal": "NO TRADE", "confidence": 0}

    macd = macd_line[-1]
    signal_line = macd_signal[-1]

    bullish = (
        ema9[-1] > ema21[-1]
        and macd > signal_line
        and 50 < rsi < 70
    )

    bearish = (
        ema9[-1] < ema21[-1]
        and macd < signal_line
        and 30 < rsi < 50
    )

    if bullish:
        signal = "CALL"
        confidence = 70
    elif bearish:
        signal = "PUT"
        confidence = 70
    else:
        signal = "NO TRADE"
        confidence = 0

    return {
        "signal": signal,
        "confidence": confidence,
        "confidence_note": "Heuristic score, not win probability",
        "rsi": rsi,
        "ema9": round(ema9[-1], 6),
        "ema21": round(ema21[-1], 6),
        "macd": round(macd, 8),
        "macd_signal": round(signal_line, 8),
        "candles_used": len(closes),
        "provider": "Biquote",
        "message": "Technical analysis only. No guaranteed prediction."
    }


@app.get("/")
def home():
    return send_from_directory(
        ".", "MAX_AI_Live_Dashboard.html"
    )


@app.get("/api/health")
def health():
    return jsonify({
        "status": "online",
        "provider": "Biquote",
        "stream": "Polling only"
    })


@app.get("/api/candles")
def candles():
    try:
        symbol = request.args.get("symbol", "EURUSD")
        tf = request.args.get("tf", "60")
        limit = request.args.get("limit", "100")

        data, cached = fetch_candles(
            symbol, tf, limit
        )

        data["cached"] = cached
        return jsonify(data)

    except requests.RequestException as error:
        return jsonify({
            "error": "Market data request failed",
            "details": str(error)
        }), 502

    except Exception as error:
        return jsonify({"error": str(error)}), 400


@app.get("/api/signal")
def signal():
    try:
        symbol = request.args.get("symbol", "EURUSD")
        tf = request.args.get("tf", "60")

        data, cached = fetch_candles(symbol, tf, 100)
        result = analyze(data["candles"])

        result["symbol"] = data["symbol"]
        result["provider"] = "Biquote"
        result["cached"] = cached
        result["last_candle_time"] = data["candles"][-1]["time"]

        return jsonify(result)

    except requests.RequestException as error:
        return jsonify({
            "signal": "NO TRADE",
            "error": "Market data request failed",
            "details": str(error)
        }), 502

    except Exception as error:
        return jsonify({
            "signal": "NO TRADE",
            "error": str(error)
        }), 400


@app.post("/api/analyze")
def analyze_route():
    try:
        payload = request.get_json(silent=True) or {}
        candles = payload.get("candles", [])

        if not isinstance(candles, list):
            return jsonify(error="Candles must be a list"), 400

        return jsonify(analyze(candles))

    except Exception as error:
        return jsonify(error=str(error)), 400


@app.get("/api/stream")
def stream():
    return jsonify({
        "status": "unavailable",
        "message": "Use candle polling. Live stream is not configured."
    }), 501


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
