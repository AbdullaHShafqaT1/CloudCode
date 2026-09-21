"""Independent checks for the controlled two-human-player tic-tac-toe benchmark.

Run from the generated workspace, with the same Python used for the application.
The benchmark prompt specifies this public contract; generated tests are separate.
"""
import importlib
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))


def main():
    Game = importlib.import_module("app_logic").Game
    game = Game()
    assert game.board == [["", "", ""] for _ in range(3)]
    assert game.turn == "X" and game.winner is None and not game.draw
    for row, col in [(-1, 0), (3, 0), (0, 3)]:
        try:
            game.move(row, col)
        except ValueError:
            pass
        else:
            raise AssertionError("Out-of-bounds move accepted")
    game.move(0, 0)
    assert game.board[0][0] == "X" and game.turn == "O"
    try:
        game.move(0, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("Occupied cell accepted")
    assert game.turn == "O"
    for move in [(1, 0), (0, 1), (1, 1), (0, 2)]:
        game.move(*move)
    assert game.winner == "X"
    try:
        game.move(2, 2)
    except ValueError:
        pass
    else:
        raise AssertionError("Move after game end accepted")
    # Both players can win; all eight winning lines are recognized.
    lines = [[(r, c) for c in range(3)] for r in range(3)]
    lines += [[(r, c) for r in range(3)] for c in range(3)]
    lines += [[(0, 0), (1, 1), (2, 2)], [(0, 2), (1, 1), (2, 0)]]
    for line in lines:
        game = Game()
        other = [(r, c) for r in range(3) for c in range(3) if (r, c) not in line]
        for move in [line[0], other[0], line[1], other[1], line[2]]:
            game.move(*move)
        assert game.winner == "X", line
    game = Game()
    for move in [(0, 0), (1, 0), (0, 1), (1, 1), (2, 2), (1, 2)]:
        game.move(*move)
    assert game.winner == "O"
    game.reset()
    assert game.board == [["", "", ""] for _ in range(3)] and game.turn == "X"
    for move in [(0, 0), (0, 1), (0, 2), (1, 1), (1, 0), (1, 2), (2, 1), (2, 0), (2, 2)]:
        game.move(*move)
    assert game.draw and game.winner is None
    print("PASS: board, legal moves, alternating humans, occupied cells, X/O wins, eight lines, draw, reset")

    server = importlib.import_module("app_gui").create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(path, data=None):
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode()

    try:
        html = request("/")
        for row in range(3):
            for col in range(3):
                assert f'cell-{row}-{col}' in html
        assert 'reset' in html and 'status' in html and 'fetch(' in html
        assert json.loads(request("/state"))["turn"] == "X"
        state = json.loads(request("/move", {"row": 0, "col": 0}))
        assert state["board"][0][0] == "X" and state["turn"] == "O"
        try:
            request("/move", {"row": 0, "col": 0})
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("HTTP endpoint accepted occupied cell")
        state = json.loads(request("/reset", {}))
        assert state["board"] == [["", "", ""] for _ in range(3)]
        print("PASS: running HTTP application, HTML board, state, move, error feedback, reset")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
