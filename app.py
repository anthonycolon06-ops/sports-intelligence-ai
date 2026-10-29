
from flask import Flask, jsonify
import os
import requests
import math
from datetime import datetime, timezone

app = Flask(__name__)

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/scoreboard"
)

HEADERS = {"User-Agent": "SportsIntelligenceAI/1.0"}


def get_scoreboard():
    response = requests.get(
        ESPN_SCOREBOARD,
        timeout=15,
        headers=HEADERS
    )
    response.raise_for_status()
    return response.json()


def extract_team(competitors, side):
    item = next(
        (
            team for team in competitors
            if team.get("homeAway") == side
        ),
        {}
    )

    team_info = item.get("team", {})
    record = None

    for entry in item.get("records", []):
        if entry.get("name") == "overall":
            record = entry.get("summary")
            break

    if record is None and item.get("records"):
        record = item["records"][0].get("summary")

    return {
        "name": team_info.get("displayName"),
        "score": item.get("score"),
        "record": record,
        "winner": item.get("winner")
    }


def parse_record(record):
    if not record or "-" not in record:
        return None

    try:
        wins, losses = record.split("-", 1)
        wins = int(wins)
        losses = int(losses)
        total = wins + losses

        if total == 0:
            return None

        return wins / total
    except (ValueError, TypeError):
        return None


def predict_winner(home, away):
    home_pct = parse_record(home.get("record"))
    away_pct = parse_record(away.get("record"))

    if home_pct is None or away_pct is None:
        return {
            "available": False,
            "reason": "Team records are not available from ESPN."
        }

    # Experimental baseline, not a validated betting model.
    edge = 2.0 * (home_pct - away_pct) + 0.15
    home_probability = 1 / (1 + math.exp(-edge))
    away_probability = 1 - home_probability

    if home_probability >= away_probability:
        predicted = home["name"]
    else:
        predicted = away["name"]

    return {
        "available": True,
        "predicted_winner": predicted,
        "home_win_probability": round(home_probability * 100, 1),
        "away_win_probability": round(away_probability * 100, 1),
        "method": "Team record plus home-court adjustment",
        "model_status": "experimental"
    }


@app.route("/")
def home():
    return jsonify({
        "app": "Sports Intelligence AI",
        "status": "online",
        "sport": "NBA",
        "version": "1.2.0",
        "message": "Sports Intelligence AI is running!"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


@app.route("/nba")
def nba():
    return jsonify({
        "sport": "NBA",
        "status": "development",
        "available_endpoints": [
            "/nba",
            "/nba/games",
            "/nba/predictions"
        ],
        "message": "NBA analysis module initialized."
    })


@app.route("/nba/games")
def nba_games():
    try:
        data = get_scoreboard()
        games = []

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            home = extract_team(competitors, "home")
            away = extract_team(competitors, "away")

            games.append({
                "game_id": event.get("id"),
                "date": event.get("date"),
                "status": event.get("status", {})
                    .get("type", {}).get("description"),
                "home_team": home["name"],
                "home_score": home["score"],
                "away_team": away["name"],
                "away_score": away["score"],
                "home_record": home["record"],
                "away_record": away["record"]
            })

        return jsonify({
            "sport": "NBA",
            "source": "ESPN unofficial scoreboard API",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "games_count": len(games),
            "games": games
        })

    except requests.RequestException:
        app.logger.exception("NBA scoreboard request failed")
        return jsonify({
            "error": "Could not retrieve NBA scoreboard"
        }), 502


@app.route("/nba/predictions")
def nba_predictions():
    try:
        data = get_scoreboard()
        predictions = []

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            home = extract_team(competitors, "home")
            away = extract_team(competitors, "away")

            status = event.get("status", {}).get("type", {})
            state = status.get("state")

            # Only predict games that have not started.
            if state != "pre":
                continue

            result = predict_winner(home, away)

            predictions.append({
                "game_id": event.get("id"),
                "date": event.get("date"),
                "home_team": home["name"],
                "away_team": away["name"],
                "home_record": home["record"],
                "away_record": away["record"],
                "prediction": result
            })

        return jsonify({
            "sport": "NBA",
            "source": "ESPN unofficial scoreboard API",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "model": "Experimental record-based baseline",
            "games_analyzed": len(predictions),
            "predictions": predictions
        })

    except requests.RequestException:
        app.logger.exception("NBA prediction request failed")
        return jsonify({
            "error": "Could not retrieve data for predictions"
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
