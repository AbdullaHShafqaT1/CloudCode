"""Observe the unchanged independent checker; never supply or repair application code."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import unittest


# Requirement groups from the ORIGINAL browser-chess prompt. A passing grouped
# test can support several requirements; partial execution of a failed test
# supports none. Requirements without coverage remain in the denominator.
REQUIREMENTS = [
    ("Initial 32-piece position and JSON state", "test_01_initial_position"),
    ("Legal movement for all six piece types", "test_02_all_piece_legal_sets"),
    ("Two human players and turn enforcement", "test_03_turn_enforcement"),
    ("Illegal/malformed moves rejected without mutation", "test_04_illegal_and_malformed_moves"),
    ("Captures and persistent move sequence", "test_05_captures_and_move_sequence"),
    ("King safety", "test_06_king_safety"),
    ("Pinned pieces", "test_07_pinned_piece"),
    ("Check detection", "test_08_check"),
    ("Checkmate, winner and postgame rejection", "test_09_checkmate_and_postgame_rejection"),
    ("Stalemate", "test_10_stalemate"),
    ("Both sides' kingside/queenside castling", "test_11_castling"),
    ("Castling restrictions", "test_12_castling_restrictions"),
    ("En passant capture", "test_13_en_passant"),
    ("En passant expiration", "test_14_en_passant_expires"),
    ("All four engine promotion choices", "test_15_all_promotions"),
    ("Engine reset", "test_16_reset"),
    ("Fifty-move and insufficient-material draws", "test_draw_handling"),
    ("Claimable repetition draw", None),
    ("Real documented CLI launch", "test_17_18_real_browser_playthrough_and_launch"),
    ("Visible 8x8 alternating board and 32 Unicode pieces", "test_17_18_real_browser_playthrough_and_launch"),
    ("Selection and legal-destination highlighting", "test_17_18_real_browser_playthrough_and_launch"),
    ("Browser two-player moves, capture and turn updates", "test_17_18_real_browser_playthrough_and_launch"),
    ("Browser new game resets position", "test_17_18_real_browser_playthrough_and_launch"),
    ("Browser checkmate and winner", "test_17_18_real_browser_playthrough_and_launch"),
    ("Browser promotion choice and draw display", "test_17_18_real_browser_playthrough_and_launch"),
    ("Browser check and stalemate displays", None),
    ("Piece accessible labels and two-player instructions", None),
    ("HTTP state, move, error JSON and reset", "test_http_rejection_and_reset"),
    ("HTTP position load through the same engine", "test_17_18_real_browser_playthrough_and_launch"),
    ("Bounded HTTP request sizes and bad JSON handling", None),
    ("Each server owns a fresh game", None),
    ("Import without starting servers/windows", "imports"),
    ("No network assets or CDN", None),
]


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}

    def startTest(self, test):
        super().startTest(test)
        self.records[test.id()] = {"status": "running", "details": []}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records[test.id()]["status"] = "passed"

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._problem(test, "failed", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._problem(test, "error", err)

    def _problem(self, test, status, err):
        self.records[test.id()]["status"] = status
        self.records[test.id()]["details"].append(self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records[test.id()].update(status="skipped", details=[reason])

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._problem(test, "failed", err)


def requirement_matrix(records, imports_passed=False, unchanged=True):
    passed = {key.rsplit('.', 1)[-1] for key, value in records.items() if value['status'] == 'passed'}
    if imports_passed:
        passed.add('imports')
    rows = [{"requirement": name, "status": "verified" if unchanged and check in passed else "unverified",
             "evidence": check if unchanged and check in passed else None} for name, check in REQUIREMENTS]
    count = sum(row['status'] == 'verified' for row in rows)
    return {"requirements": rows, "verified": count, "total": len(rows),
            "percent": round(100 * count / len(rows), 2),
            "method": "Equal-weight functional requirement groups fixed before generation; no file-count credit. "
                      "Failed grouped tests receive no partial credit. Documentation/generated tests reported separately."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('checker', type=Path)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256(args.checker.read_bytes()).hexdigest()
    sys.argv = [str(args.checker), str(args.workspace.resolve())]
    scope = runpy.run_path(str(args.checker), run_name='independent_chess_acceptance')
    before = scope['snapshot']()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(scope[name])
                              for name in ('EngineAcceptance', 'InterfaceAcceptance'))
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    unchanged = scope['snapshot']() == before and hashlib.sha256(args.checker.read_bytes()).hexdigest() == digest
    report = {"tests_run": result.testsRun, "passed": result.wasSuccessful() and not result.skipped and unchanged,
              "unchanged": unchanged, "checker_sha256": digest, "tests": result.records}
    report['functional'] = requirement_matrix(result.records, unchanged=unchanged)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
