# Copyright 2025 The game_arena Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Demo of chess, prompt generation, model generation, and parser together."""

import os
import uuid
from absl import app
from absl import flags
from databricks import sql
from game_arena.harness import databricks_sdk
from game_arena.harness import game_notation_examples
from game_arena.harness import llm_parsers
from game_arena.harness import model_generation_sdk
from game_arena.harness import parsers
from game_arena.harness import prompt_generation
from game_arena.harness import prompts
from game_arena.harness import tournament_util
import mlflow
import requests
import termcolor

import pyspiel


colored = termcolor.colored


def create_lichess_round(broadcast_id, round_name):
    """Creates a new round in a Lichess broadcast."""
    token = os.getenv("LICHESS_API_TOKEN")
    if not token:
        print("LICHESS_API_TOKEN not set. Skipping round creation.")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    data = {"name": round_name}
    url = f"https://lichess.org/api/broadcast/{broadcast_id}/new"
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["round"]["id"]
    else:
        print(f"Failed to create Lichess round: {response.text}")
        return None


def push_pgn_to_lichess(round_id, pgn):
    """Pushes a PGN to a Lichess broadcast round."""
    token = os.getenv("LICHESS_API_TOKEN")
    if not token:
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/plain",
    }
    url = f"https://lichess.org/api/broadcast/round/{round_id}/push"
    response = requests.post(url, headers=headers, data=str(pgn))
    if response.status_code != 200:
        print(f"Failed to push PGN to Lichess: {response.text}")

_NUM_MOVES = flags.DEFINE_integer(
    "num_moves",
    3,
    "Number of moves to play.",
)

_GEMINI_MODEL = flags.DEFINE_string(
    "gemini_model",
    "gemini-2.5-flash",
    "Gemini model to play as player one.",
)

_OPENAI_MODEL = flags.DEFINE_string(
    "openai_model",
    "gpt-4.1",
    "OpenAI model to play as player two.",
)

_PARSER_CHOICE = flags.DEFINE_enum_class(
    "parser_choice",
    tournament_util.ParserChoice.RULE_THEN_SOFT,
    tournament_util.ParserChoice,
    "Move parser to use.",
)


def get_model(model_name: str):
    """Returns a model instance based on the model name."""
    # All models are assumed to be served via Databricks endpoints
    # (either foundation models or external models via AI Gateway)
    return databricks_sdk.DatabricksModel(model_name=model_name)


def run_game(
    player_one_model_name: str,
    player_two_model_name: str,
    parser_choice_str: str,
    num_moves: int,
    broadcast_id: str | None = None,
    round_name: str | None = None,
):
    """Runs a single game of chess between two models."""
    # Set up game:
    pyspiel_game = pyspiel.load_game("chess")
    pyspiel_state = pyspiel_game.new_initial_state()

    # Set up prompt generator:
    prompt_generator = prompt_generation.PromptGeneratorText()
    prompt_template = prompts.PromptTemplate.NO_LEGAL_ACTIONS

    # Set up model generation:
    model_player_one = get_model(player_one_model_name)
    model_player_two = get_model(player_two_model_name)

    # Set up parser;
    parser_choice = tournament_util.ParserChoice(parser_choice_str)
    match parser_choice:
        case tournament_util.ParserChoice.RULE_THEN_SOFT:
            parser = parsers.ChainedMoveParser(
                [parsers.RuleBasedMoveParser(), parsers.SoftMoveParser("chess")]
            )
        case tournament_util.ParserChoice.LLM_ONLY:
            parser_model = model_generation_sdk.AIStudioModel(
                model_name="gemini-1.5-flash"
            )
            parser = llm_parsers.LLMParser(
                model=parser_model,
                instruction_config=llm_parsers.OpenSpielChessInstructionConfig_V0,
            )
        case _:
            raise ValueError(f"Unsupported parser choice: {parser_choice}")

    round_id = None
    if broadcast_id and round_name:
        round_id = create_lichess_round(broadcast_id, round_name)

    mlflow.set_tracking_uri("file:/tmp/mlflow")
    mlflow.set_experiment("chess-games")
    with mlflow.start_run(run_name=f"game-{uuid.uuid4()}") as run:
        mlflow.log_param("player_one_model", player_one_model_name)
        mlflow.log_param("player_two_model", player_two_model_name)
        mlflow.log_param("parser", parser_choice_str)
        mlflow.log_param("num_moves", num_moves)

        for move_number in range(num_moves):
            print(f"Pre-move debug string: {pyspiel_state.debug_string()}")
            if pyspiel_state.is_terminal():
                print(colored("Game is terminal, ending move loop.", "red"))
                break

            print(colored(f"Commencing move {move_number}...", "green"))

            with mlflow.start_span(name="generate_prompt") as span:
                # 1. Generate the prompt from the game state:
                prompt_substitutions = {
                    "readable_state_str": tournament_util.convert_to_readable_state(
                        game_short_name="chess",
                        state_str=pyspiel_state.to_string(),
                        current_player=pyspiel_state.current_player(),
                    ),
                    "move_history": (
                        tournament_util.get_action_string_history(pyspiel_state)
                        or "None"
                    ),
                    "player_name": game_notation_examples.GAME_SPECIFIC_NOTATIONS[
                        "chess"
                    ][
                        "player_map"
                    ][
                        pyspiel_state.current_player()
                    ],
                    "move_notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS[
                        "chess"
                    ][
                        "move_notation"
                    ],
                    "notation": game_notation_examples.GAME_SPECIFIC_NOTATIONS[
                        "chess"
                    ][
                        "state_notation"
                    ],
                }
                prompt = prompt_generator.generate_prompt_with_text_only(
                    prompt_template=prompt_template,
                    game_short_name="chess",
                    **prompt_substitutions,
                )
                print(colored(f"Formatted prompt: {prompt.prompt_text}", "blue"))
                span.set_inputs(prompt.prompt_text)

            with mlflow.start_span(name="call_model") as span:
                # 2. Call the model:
                if pyspiel_state.current_player() == 0:
                    model = model_player_one
                else:
                    model = model_player_two
                response = model.generate_with_text_input(prompt)
                print(
                    colored(
                        "Model player"
                        f" {pyspiel_state.current_player()} main response:"
                        f" {response.main_response}",
                        "yellow",
                    )
                )
                span.set_outputs({"response": response.main_response})

            with mlflow.start_span(name="parse_response") as span:
                # 3. Parse the model response:
                parser_input = parsers.TextParserInput(
                    text=response.main_response,
                    # TODO(google-deepmind): raw state str and readable state str should
                    # be differentiated in signatures.
                    state_str=pyspiel_state.to_string(),
                    legal_moves=parsers.get_legal_action_strings(pyspiel_state),
                    player_number=pyspiel_state.current_player(),
                )
                parser_output = parser.parse(parser_input)
                if parser_output is None:
                    print(colored("Parser output is None, ending game.", "red"))
                else:
                    print(
                        colored(f"Parser output is {parser_output}.", "magenta")
                    )
                span.set_inputs({"parser_input": parser_input})
                span.set_outputs({"parser_output": parser_output})

            # 4. Apply the move:
            pyspiel_state.apply_action(
                pyspiel_state.string_to_action(parser_output)
            )

            if round_id:
                pgn = tournament_util.get_pgn(pyspiel_state)
                push_pgn_to_lichess(round_id, pgn)

        with open("final_state.txt", "w") as f:
            f.write(pyspiel_state.to_string())
        mlflow.log_artifact("final_state.txt")

        pgn = tournament_util.get_pgn(pyspiel_state)
        game_id = uuid.uuid4().hex

        with sql.connect(
            server_hostname=os.getenv("DATABRICKS_SERVER_HOSTNAME"),
            http_path=os.getenv("DATABRICKS_HTTP_PATH"),
            access_token=os.getenv("DATABRICKS_TOKEN"),
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS chess_games (game_id STRING,"
                    " mlflow_run_id STRING, pgn STRING)"
                )
                cursor.execute(
                    "INSERT INTO chess_games (game_id, mlflow_run_id, pgn) VALUES"
                    f" ('{game_id}', '{run.info.run_id}', '{pgn}')"
                )

        returns = pyspiel_state.returns()
        if returns[0] > returns[1]:
            return (1, 0)
        elif returns[1] > returns[0]:
            return (0, 1)
        else:
            return (1, 1)  # Draw


def main(_) -> None:
    run_game(
        _GEMINI_MODEL.value,
        _OPENAI_MODEL.value,
        _PARSER_CHOICE.value.value,
        _NUM_MOVES.value,
    )


if __name__ == "__main__":
    app.run(main)
