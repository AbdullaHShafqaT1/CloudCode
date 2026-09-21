"""Independent behavioral acceptance. Never edits generated application files."""
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

import chess

WORKSPACE = Path(sys.argv[1]).resolve()
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))
sys.pycache_prefix = str(OUT / ".acceptance-bytecode")
sys.dont_write_bytecode = True


def snapshot():
    return {p.relative_to(WORKSPACE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in WORKSPACE.rglob("*") if p.is_file()
            and not any(part.startswith(".") or part in {"__pycache__", "nodelog_data"} for part in p.relative_to(WORKSPACE).parts)
            and not p.name.endswith(".bak")}


def expected_state(board):
    outcome = board.outcome(claim_draw=True)
    status = "checkmate" if board.is_checkmate() else "stalemate" if board.is_stalemate() else "draw" if outcome else "check" if board.is_check() else "playing"
    return {"fen": board.fen(), "turn": "white" if board.turn else "black",
            "pieces": {chess.square_name(sq): p.symbol() for sq, p in board.piece_map().items()},
            "legal_moves": sorted(move.uci() for move in board.legal_moves),
            "status": status, "winner": None if not outcome or outcome.winner is None else "white" if outcome.winner else "black"}


def http(base, path, data=None):
    request = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class EngineAcceptance(unittest.TestCase):
    def game(self, fen=None):
        return importlib.import_module("chess_logic").Game(fen)

    def compare(self, game, oracle):
        actual, expected = game.state(), expected_state(oracle)
        for key in expected:
            value = sorted(actual[key]) if key == "legal_moves" else actual[key]
            self.assertEqual(value, expected[key], key)

    def reject(self, game, move):
        before = game.state()
        with self.assertRaises(ValueError):
            game.move(move)
        self.assertEqual(game.state(), before)

    def test_01_initial_position(self):
        game = self.game()
        self.compare(game, chess.Board())
        self.assertEqual(len(game.state()["pieces"]), 32)
        self.assertEqual(len(game.state()["legal_moves"]), 20)

    def test_02_all_piece_legal_sets(self):
        # Known, legal positions exercise unobstructed sliding pieces and jumps.
        positions = [chess.STARTING_FEN,
                     "4k3/8/8/8/3Q4/8/8/4K3 w - - 0 1",
                     "4k3/8/8/8/3R4/8/8/4K3 w - - 0 1",
                     "4k3/8/8/8/3B4/8/8/4K3 w - - 0 1",
                     "4k3/8/8/8/3N4/8/8/4K3 w - - 0 1",
                     "4k3/8/8/8/3P4/8/8/4K3 w - - 0 1"]
        for fen in positions:
            with self.subTest(fen=fen):
                oracle = chess.Board(fen)
                self.assertTrue(oracle.is_valid(), "Invalid independent fixture")
                self.compare(self.game(fen), oracle)

    def test_03_turn_enforcement(self):
        game = self.game()
        self.reject(game, "e7e5")
        game.move("e2e4")
        self.reject(game, "d2d4")

    def test_04_illegal_and_malformed_moves(self):
        game = self.game()
        for move in ("e2e5", "a1a8", "e1e2", "e2d3", "a3a4", "garbage", "", "e2e4q"):
            with self.subTest(move=move):
                self.reject(game, move)

    def test_05_captures_and_move_sequence(self):
        game, oracle = self.game(), chess.Board()
        for move in ("e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5d8", "g1f3"):
            game.move(move)
            oracle.push_uci(move)
            self.compare(game, oracle)
        self.assertEqual(len(game.state()["pieces"]), 30)

    def test_06_king_safety(self):
        game = self.game("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
        self.reject(game, "e1d2")

    def test_07_pinned_piece(self):
        game = self.game("k3r3/8/8/8/8/8/4R3/4K3 w - - 0 1")
        self.reject(game, "e2d2")
        game.move("e2e8")
        self.assertEqual(game.state()["pieces"].get("e8"), "R")

    def test_08_check(self):
        game = self.game("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
        self.assertEqual(game.state()["status"], "check")

    def test_09_checkmate_and_postgame_rejection(self):
        game = self.game()
        for move in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.move(move)
        self.assertEqual(game.state()["status"], "checkmate")
        self.assertEqual(game.state()["winner"], "black")
        self.assertEqual(game.state()["legal_moves"], [])
        self.reject(game, "a2a3")

    def test_10_stalemate(self):
        game = self.game("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        self.assertEqual(game.state()["status"], "stalemate")
        self.assertIsNone(game.state()["winner"])

    def test_11_castling(self):
        for turn, move, rook in (("w", "e1g1", "f1"), ("w", "e1c1", "d1"), ("b", "e8g8", "f8"), ("b", "e8c8", "d8")):
            fen = f"r3k2r/8/8/8/8/8/8/R3K2R {turn} KQkq - 0 1"
            game, oracle = self.game(fen), chess.Board(fen)
            game.move(move)
            oracle.push_uci(move)
            self.compare(game, oracle)
            self.assertEqual(game.state()["pieces"].get(rook), "R" if turn == "w" else "r")

    def test_12_castling_restrictions(self):
        for fen in (chess.STARTING_FEN, "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1",
                    "r3kr1r/8/8/8/8/8/8/R3K2R w KQ - 0 1",
                    "r3k2r/8/8/8/8/8/4r3/R3K2R w KQ - 0 1"):
            self.reject(self.game(fen), "e1g1")

    def test_13_en_passant(self):
        game, oracle = self.game(), chess.Board()
        for move in ("e2e4", "a7a6", "e4e5", "d7d5", "e5d6"):
            game.move(move)
            oracle.push_uci(move)
        self.compare(game, oracle)
        self.assertNotIn("d5", game.state()["pieces"])
        self.assertEqual(game.state()["pieces"]["d6"], "P")

    def test_14_en_passant_expires(self):
        game = self.game()
        for move in ("e2e4", "a7a6", "e4e5", "d7d5", "a2a3", "a6a5"):
            game.move(move)
        self.reject(game, "e5d6")

    def test_15_all_promotions(self):
        fen = "7k/P7/8/8/8/8/8/4K3 w - - 0 1"
        for piece in "qrbn":
            game = self.game(fen)
            game.move("a7a8" + piece)
            self.assertEqual(game.state()["pieces"]["a8"], piece.upper())

    def test_16_reset(self):
        game = self.game()
        game.move("e2e4")
        game.reset()
        self.compare(game, chess.Board())

    def test_draw_handling(self):
        self.assertEqual(self.game("7k/8/8/8/8/8/8/K7 w - - 0 1").state()["status"], "draw")
        self.assertEqual(self.game("7k/8/8/8/8/8/8/KR6 w - - 100 51").state()["status"], "draw")


@contextmanager
def launched_app():
    # Uses the documented CLI contract, not an in-process replacement server.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [sys.executable, "main.py", "--host", "127.0.0.1", "--port", str(port)]
    log = (OUT / "chess-launch.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=WORKSPACE, stdout=log, stderr=log,
                            creationflags=0x08000000 if os.name == "nt" else 0)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"Documented launch exited {proc.returncode}; see chess-launch.log")
            try:
                if http(base, "/state")[0] == 200:
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError("Documented launch did not become ready in 10 seconds")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        log.close()


class InterfaceAcceptance(unittest.TestCase):
    def test_17_18_real_browser_playthrough_and_launch(self):
        from playwright.sync_api import sync_playwright
        evidence = OUT / "screenshots" / str(time.time_ns())
        evidence.mkdir(parents=True, exist_ok=False)
        with launched_app() as base, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(base)
                page.wait_for_selector('#sq-e2[data-piece="P"]')
                self.assertEqual(page.locator("[data-square]").count(), 64)
                populated = page.locator('[data-square][data-piece]:not([data-piece=""])')
                self.assertEqual(populated.count(), 32)
                self.assertTrue(all(text.strip() for text in populated.all_inner_texts()), "Pieces must be visibly rendered, not only stored in attributes")
                boxes = [page.locator("#sq-" + square).bounding_box() for square in ("a8", "b8", "h8", "a7", "a1")]
                self.assertTrue(all(box and box["width"] > 10 and box["height"] > 10 for box in boxes))
                self.assertLess(boxes[0]["x"], boxes[1]["x"])
                self.assertLess(boxes[1]["x"], boxes[2]["x"])
                self.assertLess(boxes[0]["y"], boxes[3]["y"])
                self.assertLess(boxes[3]["y"], boxes[4]["y"])
                colors = [page.locator('#sq-' + square).evaluate("el => getComputedStyle(el).backgroundColor") for square in ("a1", "b1", "a2", "b2")]
                self.assertNotEqual(colors[0], colors[1])
                self.assertEqual(colors[0], colors[3])
                self.assertEqual(colors[1], colors[2])
                self.assertTrue(page.locator("#board").is_visible())
                self.assertIn("white", page.locator("#status").inner_text().lower())
                page.screenshot(path=str(evidence / "01-initial.png"), full_page=True)
                page.click("#sq-e2")
                self.assertIn("selected", page.locator("#sq-e2").get_attribute("class") or "")
                self.assertIn("legal", page.locator("#sq-e4").get_attribute("class") or "")
                page.screenshot(path=str(evidence / "02-selection.png"), full_page=True)
                page.click("#sq-e4")
                page.wait_for_selector('#sq-e4[data-piece="P"]')
                self.assertIn("black", page.locator("#status").inner_text().lower())
                for source, target, piece in (("d7", "d5", "p"), ("e4", "d5", "P")):
                    page.click("#sq-" + source)
                    page.click("#sq-" + target)
                    page.wait_for_selector(f'#sq-{target}[data-piece="{piece}"]')
                self.assertEqual(len(http(base, "/state")[1]["pieces"]), 31)
                page.screenshot(path=str(evidence / "03-capture.png"), full_page=True)
                page.click("#new-game")
                page.wait_for_selector('#sq-e2[data-piece="P"]')
                for source, target, piece in (("f2", "f3", "P"), ("e7", "e5", "p"), ("g2", "g4", "P"), ("d8", "h4", "q")):
                    page.click("#sq-" + source)
                    page.click("#sq-" + target)
                    page.wait_for_selector(f'#sq-{target}[data-piece="{piece}"]')
                page.wait_for_function("document.querySelector('#status').textContent.toLowerCase().includes('checkmate')")
                self.assertEqual(http(base, "/state")[1]["winner"], "black")
                page.screenshot(path=str(evidence / "04-checkmate.png"), full_page=True)
                # The same real UI must apply an explicit underpromotion choice.
                status, state = http(base, "/position", {"fen": "7k/P7/8/8/8/8/8/4K3 w - - 0 1"})
                self.assertEqual(status, 200)
                page.reload()
                page.wait_for_selector('#sq-a7[data-piece="P"]')
                page.select_option("#promotion", "n")
                page.click("#sq-a7")
                page.click("#sq-a8")
                page.wait_for_selector('#sq-a8[data-piece="N"]')
                page.wait_for_function("document.querySelector('#status').textContent.toLowerCase().includes('draw')")
                page.screenshot(path=str(evidence / "05-underpromotion.png"), full_page=True)
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_http_rejection_and_reset(self):
        with launched_app() as base:
            initial = http(base, "/state")[1]
            code, value = http(base, "/move", {"uci": "e7e5"})
            self.assertEqual(code, 400)
            self.assertTrue(value.get("error"))
            self.assertEqual(http(base, "/state")[1], initial)
            self.assertEqual(http(base, "/move", {"uci": "e2e4"})[0], 200)
            self.assertEqual(http(base, "/reset", {})[1], initial)

    def test_19_documentation_and_generated_tests(self):
        for name in ("README.md", "RunningGUIDE.txt"):
            content = (WORKSPACE / name).read_text(encoding="utf-8")
            self.assertIn("python main.py --host 127.0.0.1 --port 8765", content)
            self.assertIn("python-chess", content.lower())
        self.assertIn("python-chess", (WORKSPACE / "requirements.txt").read_text().lower())
        suite = unittest.TestLoader().discover(str(WORKSPACE), pattern="test*.py")
        result = unittest.TestResult()
        suite.run(result)
        self.assertGreaterEqual(result.testsRun, 5)
        self.assertTrue(result.wasSuccessful(), str(result.errors + result.failures))
        self.assertFalse(result.skipped)


if __name__ == "__main__":
    before = snapshot()
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (EngineAcceptance, InterfaceAcceptance)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    unchanged = snapshot() == before
    print("FINAL_FILES_UNCHANGED=" + str(unchanged))
    sys.exit(0 if result.wasSuccessful() and not result.skipped and unchanged else 1)
