import itertools
import os
import requests
from flask import Flask, render_template, request, redirect, url_for
from game_arena.harness import harness_demo

app = Flask(__name__)

tournament_results = {}


def create_lichess_broadcast(tournament_name):
    token = os.getenv("LICHESS_API_TOKEN")
    if not token:
        print("LICHESS_API_TOKEN not set. Skipping broadcast.")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    data = {"name": tournament_name, "description": "A tournament played by AI agents."}
    response = requests.post("https://lichess.org/api/broadcast/new", headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["tour"]["id"]
    else:
        print(f"Failed to create Lichess broadcast: {response.text}")
        return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start_game', methods=['POST'])
def start_game():
    player1 = request.form.get('player1')
    player2 = request.form.get('player2')
    parser = request.form.get('parser')

    print(f"Player 1: {player1}")
    print(f"Player 2: {player2}")
    print(f"Parser: {parser}")

    harness_demo.run_game(
        player_one_model_name=player1,
        player_two_model_name=player2,
        parser_choice_str=parser,
        num_moves=10,
    )

    return "Game started! Check the console for details."


@app.route('/start_tournament', methods=['POST'])
def start_tournament():
    players = request.form.getlist('players')
    parser = request.form.get('parser')

    if len(players) < 2:
        return "Please select at least 2 players for the tournament."

    broadcast_id = create_lichess_broadcast("AI Tournament")
    if not broadcast_id:
        return "Failed to create Lichess broadcast."

    matches = list(itertools.combinations(players, 2))

    results = []
    for player1, player2 in matches:
        print(f"Starting game between {player1} and {player2}")
        result = harness_demo.run_game(
            player_one_model_name=player1,
            player_two_model_name=player2,
            parser_choice_str=parser,
            num_moves=10,
            broadcast_id=broadcast_id,
            round_name=f"{player1} vs {player2}",
        )
        results.append(result)

    teams = [list(match) for match in matches]

    global tournament_results
    tournament_results = {
        "teams": teams,
        "results": [results]
    }

    return redirect(url_for('tournament_results_page'))

@app.route('/tournament_results')
def tournament_results_page():
    return render_template('tournament_results.html', data=tournament_results)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
