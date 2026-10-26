from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "app": "Sports Intelligence AI",
        "status": "online",
        "sport": "NBA",
        "version": "1.0.0",
        "message": "Sports Intelligence AI is running!"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    }), 200


@app.route("/nba")
def nba():
    return jsonify({
        "sport": "NBA",
        "status": "development",
        "features": [
            "Game analysis",
            "Win probability estimates",
            "Player statistics",
            "Kalshi market analysis"
        ],
        "message": "NBA analysis module initialized."
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
