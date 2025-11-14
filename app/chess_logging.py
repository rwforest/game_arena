from __future__ import annotations
import os, time, json, pathlib
import chess, chess.pgn, chess.svg
OUT_DIR = os.environ.get("GA_CHESS_OUT_DIR", "runs/games")
class ChessRecorder:
    def __init__(self, headers: dict | None = None, *, write_files: bool = True, out_dir: str | None = None):
        self._headers = headers or {}
        self._moves_uci: list[str] = []
        self._moves_san: list[str] = []
        self._fens: list[str] = []
        self._board = chess.Board()
        self._write_files = write_files
        self._out_dir = out_dir if out_dir is not None else OUT_DIR
    def push_uci(self, uci: str):
        mv = chess.Move.from_uci(uci)
        san = self._board.san(mv)
        self._board.push(mv)
        self._moves_uci.append(uci)
        self._moves_san.append(san)
        self._fens.append(self._board.fen())
    def push_san(self, san: str):
        mv = self._board.parse_san(san)
        self._board.push(mv)
        self._moves_uci.append(mv.uci())
        self._moves_san.append(san)
        self._fens.append(self._board.fen())
    def finalize(self, result_str: str | None = None) -> dict:
        # Build PGN from recorded moves
        game = chess.pgn.Game()
        for k, v in self._headers.items():
            game.headers[k] = v
        if result_str:
            game.headers["Result"] = result_str
        node = game
        board = chess.Board()
        for uci in self._moves_uci:
            mv = chess.Move.from_uci(uci)
            node = node.add_variation(mv)
            board.push(mv)
        pgn_str = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        wrote = False
        pgn_fp = json_fp = svg_fp = None
        if self._write_files and self._out_dir:
            pathlib.Path(self._out_dir).mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            base = os.path.join(self._out_dir, f"game-{ts}")
            pgn_fp = base + ".pgn"
            json_fp = base + ".json"
            svg_fp = base + "-final.svg"
            with open(pgn_fp, "w", encoding="utf-8") as f:
                f.write(pgn_str)
            with open(json_fp, "w", encoding="utf-8") as f:
                json.dump(
                    {"headers": self._headers, "result": result_str, "moves_uci": self._moves_uci,
                     "moves_san": self._moves_san, "fens": self._fens},
                    f, indent=2,
                )
            with open(svg_fp, "w", encoding="utf-8") as f:
                f.write(chess.svg.board(board=board))
            wrote = True
        return {
            "pgn_str": pgn_str,
            "moves": len(self._moves_uci),
            "pgn": pgn_fp if wrote else None,
            "json": json_fp if wrote else None,
            "final_svg": svg_fp if wrote else None,
        }
