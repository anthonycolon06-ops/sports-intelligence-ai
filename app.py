
from flask import Flask, jsonify, request
import os
import requests
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

app = Flask(__name__)

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/scoreboard"
)

HEADERS = {
    "User-Agent": "SportsIntelligenceAI/1.3"
}

PR_TIMEZONE = ZoneInfo("America/Puerto_Rico")
CACHE_SECONDS = 300

scoreboard_cache = {}
cache_lock = Lock()


def get_pr_date():
    return datetime.now(PR_TIMEZONE).date()


def fetch_scoreboard(game_date):
    cache_key = game_date.isoformat()
    now = datetime.now(timezone.utc).timestamp()

    with cache_lock:
        cached = scoreboard_cache.get(cache_key)

        if cached and now - cached["time"] < CACHE_SECONDS:
            return cached["events"]

    response = requests.get(
        ESPN_SCOREBOARD,
        params={
            "dates": game_date.strftime("%Y%m%d"),
            "limit": 100
        },
        timeout=10,
        headers=HEADERS
    )
    response.raise_for_status()

    events = response.json().get("events", [])

    with cache_lock:
        scoreboard_cache[cache_key] = {
            "time": now,
            "events": events
        }

    return events


def collect_events(start_date, end_date):
    """Retrieve scoreboard data for a range of calendar dates."""
    dates = []
    current = start_date

    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)

    events_by_id = {}
    failed_dates = []

    # Concurrent requests reduce total waiting time.
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_scoreboard, day): day
            for day in dates
        }

        for future in as_completed(futures):
            day = futures[future]

            try:
                events = future.result()

                for event in events:
                    event_id = event.get("id")

                    if event_id:
                        events_by_id[event_id] = event

            except requests.RequestException:
                app.logger.warning(
                    "Could not retrieve ESPN data for %s",
                    day.isoformat()
                )
                failed_dates.append(day.isoformat())

    events = list(events_by_id.values())
    events.sort(key=lambda item: item.get("date", ""))

    return events, failed_dates


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


def numeric_score(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def build_recent_stats(events, cutoff_date):
    """Calculate each team's recent performance from completed games."""
    team_games = {}

    for event in events:
        game = normalize_game(event)

        if game["state"] != "post":
            continue

        if not game["date"]:
            continue

        try:
            game_date = datetime.fromisoformat(
                game["date"].replace("Z", "+00:00")
            ).astimezone(PR_TIMEZONE).date()
        except ValueError:
            continue

        if game_date >= cutoff_date:
            continue

        home_score = numeric_score(game["home"]["score"])
        away_score = numeric_score(game["away"]["score"])

        if home_score is None or away_score is None:
            continue

        home_name = game["home"]["name"]
        away_name = game["away"]["name"]

        if not home_name or not away_name:
            continue

        home_entry = {
            "date": game["date"],
            "points_for": home_score,
            "points_against": away_score,
            "won": home_score > away_score
        }

        away_entry = {
            "date": game["date"],
            "points_for": away_score,
            "points_against": home_score,
            "won": away_score > home_score
        }

        team_games.setdefault(home_name, []).append(home_entry)
        team_games.setdefault(away_name, []).append(away_entry)

    stats = {}

    for team, games in team_games.items():
        games.sort(key=lambda item: item["date"], reverse=True)
        recent = games[:5]

        if not recent:
            continue

        count = len(recent)
        wins = sum(1 for game in recent if game["won"])

        points_for = sum(
            game["points_for"] for game in recent
        ) / count

        points_against = sum(
            game["points_against"] for game in recent
        ) / count

        stats[team] = {
            "games_used": count,
            "wins": wins,
            "losses": count - wins,
            "win_rate": wins / count,
            "avg_points_for": round(points_for, 1),
            "avg_points_against": round(points_against, 1),
            "avg_point_differential": round(
                points_for - points_against, 1
            )
        }

    return stats


def predict_winner(home, away, recent_stats):
    home_pct = parse_record(home.get("record"))
    away_pct = parse_record(away.get("record"))

    home_recent = recent_stats.get(home.get("name"))
    away_recent = recent_stats.get(away.get("name"))

    if home_pct is None or away_pct is None:
        return {
            "available": False,
            "reason": "Season records are unavailable from ESPN."
        }

    if not home_recent or not away_recent:
        return {
            "available": False,
            "reason": "Insufficient recent game statistics."
        }

    # Experimental expected-margin model:
    # season record + last-five form + point differential + home court.
    expected_margin = (
        5.0 * (home_pct - away_pct)
        + 2.0 * (
            home_recent["win_rate"]
            - away_recent["win_rate"]
        )
        + 0.25 * (
            home_recent["avg_point_differential"]
            - away_recent["avg_point_differential"]
        )
        + 2.5
    )

    # Convert estimated margin into a probability.
    home_probability = 1 / (
        1 + math.exp(-expected_margin / 6.5)
    )

    # Avoid presenting extreme estimates as certainties.
    home_probability = max(
        0.05, min(0.95, home_probability)
    )
    away_probability = 1 - home_probability

    predicted = (
        home["name"]
        if home_probability >= 0.5
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
        "estimated_margin_home": round(expected_margin, 1),
        "home_recent_form": home_recent,
        "away_recent_form": away_recent,
        "method": (
            "Season record, last-five performance, "
            "point differential and home-court adjustment"
        ),
        "model_status": "experimental_unvalidated"
    }


@app.route("/")
def home():
    return jsonify({
        "app": "Sports Intelligence AI",
        "status": "online",
        "sport": "NBA",
        "version": "1.4.0",
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
            "/nba/predictions?days=5"
        ],
        "message": "NBA analysis module initialized."
    })


@app.route("/nba/games")
def nba_games():
    today = get_pr_date()

    try:
        raw_events = fetch_scoreboard(today)
        games = [
            normalize_game(event)
            for event in raw_events
        ]

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
            "requested_date": today.isoformat(),
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
    today = get_pr_date()

    try:
        days = int(request.args.get("days", "3"))
    except ValueError:
        days = 3

    days = max(0, min(days, 7))

    # Collect recent history plus today's and future games.
    history_start = today - timedelta(days=10)
    future_end = today + timedelta(days=days)

    events, failed_dates = collect_events(
        history_start,
        future_end
    )

    recent_stats = build_recent_stats(events, today)
    predictions = []

    for event in events:
        game = normalize_game(event)

        if game["state"] != "pre":
            continue

        if not game["date"]:
            continue

        try:
            game_date = datetime.fromisoformat(
                game["date"].replace("Z", "+00:00")
            ).astimezone(PR_TIMEZONE).date()
        except ValueError:
            continue

        if game_date < today or game_date > future_end:
            continue

        home = game["home"]
        away = game["away"]

        result = predict_winner(
            home,
            away,
            recent_stats
        )

        predictions.append({
            "game_id": game["game_id"],
            "date": game["date"],
            "home_team": home["name"],
            "away_team": away["name"],
            "home_record": home["record"],
            "away_record": away["record"],
            "prediction": result
        })

    predictions.sort(key=lambda item: item["date"] or "")

    return jsonify({
        "sport": "NBA",
        "source": "ESPN unofficial scoreboard API",
        "timezone": "America/Puerto_Rico",
        "requested_from": today.isoformat(),
        "days_ahead": days,
        "history_days_used": 10,
        "recent_games_per_team": 5,
        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model": "Recent-form experimental baseline",
        "model_status": "not_yet_backtested",
        "historical_data_available_for_teams": len(recent_stats),
        "failed_dates": failed_dates,
        "games_analyzed": len(predictions),
        "predictions": predictions
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
