from flask import Flask, jsonify
import os
import requests
from datetime import datetime

app = Flask(__name__)

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/scoreboard"
)


@app.route("/")
def home():
    return jsonify({
        "app": "Sports Intelligence AI",
        "status": "online",
        "sport": "NBA",
        "version": "1.1.0",
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
            "/nba/games",
            "/nba"
        ],
        "message": "NBA analysis module initialized."
    })


@app.route("/nba/games")
def nba_games():
    try:
        response = requests.get(
            ESPN_SCOREBOARD,
            timeout=15,
            headers={"User-Agent": "SportsIntelligenceAI/1.0"}
        )
        response.raise_for_status()
        data = response.json()

        games = []

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            home_team = next(
                (team for team in competitors
                 if team.get("homeAway") == "home"),
                {}
            )

            away_team = next(
                (team for team in competitors
                 if team.get("homeAway") == "away"),
                {}
            )

            games.append({
                "game_id": event.get("id"),
                "date": event.get("date"),
                "status": event.get("status", {})
                               .get("type", {})
                               .get("description"),
                "home_team": home_team.get("team", {}).get("displayName"),
                "home_score": home_team.get("score"),
                "away_team": away_team.get("team", {}).get("displayName"),
                "away_score": away_team.get("score")
            })

        return jsonify({
            "sport": "NBA",
            "source": "ESPN unofficial scoreboard API",
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
            "games_count": len(games),
            "games": games
        })

    except requests.RequestException as error:
        app.logger.exception("NBA scoreboard request failed")
        return jsonify({
            "error": "Could not retrieve NBA scoreboard",
            "details": str(error)
        }), 502

    except (ValueError, KeyError, IndexError, TypeError):
        app.logger.exception("Unexpected NBA scoreboard response")
        return jsonify({
            "error": "Unexpected response from the scoreboard source"
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
