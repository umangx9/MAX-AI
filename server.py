import os
import json
import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OTCHARTS_API_KEY", "").strip()
BASE = "https://otcharts.com"

app = Flask(__name__)

def headers():
    if not API_KEY:
        raise RuntimeError("API key server par set nahi hai")
    return {"Authorization": f"Bearer {API_KEY}"}

@app.get("/")
def home():
    return send_from_directory(".", "MAX_AI_Live_Dashboard.html")

@app.get("/api/candles")
def candles():
    try:
        params = {
            k: request.args[k]
            for k in ("venue", "symbol", "tf", "limit")
            if k in request.args
        }
        r = requests.get(
            f"{BASE}/v1/candles",
            params=params,
            headers=headers(),
            timeout=20
        )
        return (r.text, r.status_code,
                {"Content-Type": "application/json"})
    except Exception as e:
        return jsonify(error=str(e)), 503

@app.get("/api/stream")
def stream():
    venue = request.args.get("venue", "quotex")
    symbol = request.args.get("symbol", "EURUSD_otc")

    def generate():
        try:
            with requests.get(
                f"{BASE}/v1/stream",
                params={"venue": venue, "symbol": symbol},
                headers=headers(),
                stream=True,
                timeout=(15, None)
            ) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if line:
                        yield line + "\n"
                    else:
                        yield "\n"
        except Exception as e:
            yield "event: error\ndata: " + json.dumps(
                {"error": str(e)}
            ) + "\n\n"

    return Response(generate(), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
