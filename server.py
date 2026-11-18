
import os
import time
import requests

from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_KEY = (
    os.getenv("OTCHARTS_API_KEY")
    or os.getenv("API_KEY")
    or ""
).strip()

BASE_URL = "https://otcharts.com"

# API request cache
CACHE = {}
CACHE_SECONDS = 300


def api_headers():
    if not API_KEY:
        raise RuntimeError("OTCharts API key is missing")

    return {
        "Authorization": f"Bearer {API_KEY}"
    }


def get_candles(params):
    cache_key = str(sorted(params.items()))
    now = time.time()

    if cache_key in CACHE:
        saved_time, saved_data = CACHE[cache_key]

        if now - saved_time < CACHE_SECONDS:
            return saved_data, True

    response = requests.get(
        BASE_URL + "/v1/candles",
        params=params,
        headers=api_headers(),
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    CACHE[cache_key] = (now, data)

    return data, False


def extract_candles(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("candles", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]

    return []


def ema(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)

    result = [sum(values[:period]) / period]

    for value in values[period:]:
        result.append(
            (value - result[-1]) * multiplier + result[-1]
        )

    return result


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
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


def analyze_candles(candles):
    closes = []

    for candle in candles:
        try:
            closes.append(float(candle["close"]))
        except (KeyError, TypeError, ValueError):
            continue

    if len(closes) < 35:
        return {
            "signal": "NO TRADE",
            "confidence": 0,
            "error": "At least 35 valid candles are required"
        }

    rsi = calculate_rsi(closes)

    fast = ema(closes, 12)
    slow = ema(closes, 26)

    if not fast or not slow or rsi is None:
        return {
            "signal": "NO TRADE",
            "confidence": 0,
            "error": "Not enough indicator data"
        }

    macd = fast[-1] - slow[-1]
    signal_line_values = [
        fast[i + 26 - 12] - slow[i]
        for i in range(len(slow))
    ]

    macd_signal = ema(signal_line_values, 9)

    if not macd_signal:
        return {
            "signal": "NO TRADE",
            "confidence": 0
        }

    macd_signal_value = macd_signal[-1]

    ema_fast = ema(closes, 9)[-1]
    ema_slow = ema(closes, 21)[-1]

    bullish = (
        ema_fast > ema_slow
        and macd > macd_signal_value
        and 50 < rsi < 70
    )

    bearish = (
        ema_fast < ema_slow
        and macd < macd_signal_value
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
        "rsi": rsi,
        "ema_fast": round(ema_fast, 6),
        "ema_slow": round(ema_slow, 6),
        "macd": round(macd, 8),
        "macd_signal": round(macd_signal_value, 8),
        "candles_used": len(closes),
        "message": "Technical analysis only; not a guaranteed prediction."
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
        "api_key_configured": bool(API_KEY),
        "stream": "Not enabled in this demo"
    })


@app.get("/api/candles")
def candles():
    try:
        params = {
            key: request.args[key]
            for key in ("venue", "symbol", "tf", "limit")
            if key in request.args
        }

        if not params.get("symbol"):
            return jsonify(error="Symbol is required"), 400

        data, cached = get_candles(params)

        return jsonify({
            "source": "OTCharts",
            "cached": cached,
            "data": data
        })

    except requests.RequestException as error:
        return jsonify({
            "error": "Candle API request failed",
            "details": str(error)
        }), 502

    except Exception as error:
        return jsonify(error=str(error)), 503


@app.post("/api/analyze")
def analyze():
    try:
        payload = request.get_json(silent=True) or {}
        candles = extract_candles(payload)

        result = analyze_candles(candles)

        return jsonify(result)

    except Exception as error:
        return jsonify(error=str(error)), 400


@app.get("/api/stream")
def stream():
    return jsonify({
        "status": "unavailable",
        "message": (
            "Live Stream is not enabled. "
            "Use manual candle loading on the Free Plan."
        )
    }), 501


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
