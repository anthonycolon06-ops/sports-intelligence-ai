
from flask import Flask, jsonify
import os
import requests
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

app = Flask(__name__)

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/scoreboard"
)

HEADERS = {
    "User-Agent": "SportsIntelligenceAI/1.2"
}

PR_TIMEZONE = ZoneInfo("America/Puerto_Rico")


def get_pr_date():
    return datetime.now(PR_TIMEZONE).date()


def get_scoreboard(game_date=None):
    if game_date is None:
        game_date = get_pr_date()

    params = {
        "dates": game_date.strftime("%Y%m%d"),
        "limit": 100
    }

    response = requests.get(
        ESPN_SCOREBOARD,
        params=params,
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

    # Modelo experimental: récord y ventaja de jugar en casa.
    edge = 2.0 * (home_pct - away_pct) + 0.15

    home_probability = 1 / (1 + math.exp(-edge))
    away_probability = 1 - home_probability

    predicted = (
        home["name"]
        if home_probability >= away_probability
        else away["name"]
    )

    return {
        "available": True,
        "predicted_winner": predicted,
        "home_win_probability": round(
            home_probability * 100, 1
        ),
        "away_win_probability": round(
            away_probability * 100, 1
        ),
        "method": "Team record plus home-court adjustment",
        "model_status": "experimental"
    }


def normalize_game(event):
    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])

    home = extract_team(competitors, "home")
    away = extract_team(competitors, "away")

    status_info = event.get("status", {})
    status_type = status_info.get("type", {})

    return {
        "game_id": event.get("id"),
        "date": event.get("date"),
        "status": status_type.get("description"),
        "state": status_type.get("state"),
        "home": home,
        "away": away
    }


def collect_games(days_ahead=0):
    games = []
    seen_ids = set()
    today = get_pr_date()

    for offset in range(days_ahead + 1):
        game_date = today + timedelta(days=offset)
        data = get_scoreboard(game_date)

        for event in data.get("events", []):
            game = normalize_game(event)
            game_id = game.get("game_id")

            if game_id and game_id not in seen_ids:
                seen_ids.add(game_id)
                games.append(game)

    return games


@app.route("/")
def home():
    return jsonify({
        "app": "Sports Intelligence AI",
        "status": "online",
        "sport": "NBA",
        "version": "1.3.0",
        "message": "Sports Intelligence AI is running!"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timezone": "America/Puerto_Rico",
        "local_date": get_pr_date().isoformat()
    }), 200


@app.route("/nba")
def nba():
    return jsonify({
        "sport": "NBA",
        "status": "development",
        "available_endpoints": [
            "/nba",
            "/nba/games",
            "/nba/predictions",
            "/nba/predictions?days=3"
        ],
        "message": "NBA analysis module initialized."
    })


@app.route("/nba/games")
def nba_games():
    try:
        games = collect_games(days_ahead=0)

        output = []

        for game in games:
            output.append({
                "game_id": game["game_id"],
                "date": game["date"],
                "status": game["status"],
                "home_team": game["home"]["name"],
                "home_score": game["home"]["score"],
                "home_record": game["home"]["record"],
                "away_team": game["away"]["name"],
                "away_score": game["away"]["score"],
                "away_record": game["away"]["record"]
            })

        return jsonify({
            "sport": "NBA",
            "source": "ESPN unofficial scoreboard API",
            "timezone": "America/Puerto_Rico",
            "requested_date": get_pr_date().isoformat(),
            "retrieved_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "games_count": len(output),
            "games": output
        })

    except requests.RequestException:
        app.logger.exception("NBA scoreboard request failed")
        return jsonify({
            "error": "Could not retrieve NBA scoreboard"
        }), 502


@app.route("/nba/predictions")
def nba_predictions():
    try:
        days = request_days()
        games = collect_games(days_ahead=days)

        predictions = []

        for game in games:
            # ESPN state 'pre' significa que el partido no ha comenzado.
            if game["state"] != "pre":
                continue

            home = game["home"]
            away = game["away"]

            result = predict_winner(home, away)

            predictions.append({
                "game_id": game["game_id"],
                "date": game["date"],
                "home_team": home["name"],
                "away_team": away["name"],
                "home_record": home["record"],
                "away_record": away["record"],
                "prediction": result
            })

        return jsonify({
            "sport": "NBA",
            "source": "ESPN unofficial scoreboard API",
            "timezone": "America/Puerto_Rico",
            "requested_from": get_pr_date().isoformat(),
            "days_ahead": days,
            "retrieved_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "model": "Experimental record-based baseline",
            "games_analyzed": len(predictions),
            "predictions": predictions
        })

    except requests.RequestException:
        app.logger.exception("NBA prediction request failed")
        return jsonify({
            "error": "Could not retrieve data for predictions"
        }), 502


def request_days():
    from flask import request

    try:
        days = int(request.args.get("days", "3"))
    except ValueError:
        days = 3

    # Evita consultar rangos excesivamente grandes.
    return max(0, min(days, 7))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
