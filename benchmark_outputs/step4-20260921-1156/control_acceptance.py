"""Independent behavior checks; no generated implementation is supplied here."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

WORKSPACE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(WORKSPACE))
sys.pycache_prefix = str(WORKSPACE / ".cloudcode" / "independent-bytecode")
sys.dont_write_bytecode = True


class ControlAcceptance(unittest.TestCase):
    def setUp(self):
        self.summarize = importlib.import_module("app_logic").summarize

    def test_values_and_input_preservation(self):
        for values in ([1, -2, 3], (4,), [-10, -2, -7], [10**30, -10**30, 6]):
            original = list(values)
            result = self.summarize(values)
            self.assertEqual(result, {"count": len(values), "total": sum(values), "mean": sum(values)/len(values), "minimum": min(values), "maximum": max(values)})
            self.assertEqual(list(values), original)

    def test_empty_rejected(self):
        for values in ([], ()):
            with self.assertRaises(ValueError):
                self.summarize(values)

    def test_invalid_types_rejected(self):
        for values in (None, "123", 3, {1, 2}, [True], [1, False], [1.5], ["2"], [None]):
            with self.subTest(values=values), self.assertRaises(TypeError):
                self.summarize(values)

    def test_cli_and_presentation(self):
        expected = {"count": 3, "total": 2, "mean": 2/3, "minimum": -2, "maximum": 3}
        from app_gui import format_summary
        self.assertEqual(json.loads(format_summary(expected)), expected)
        res = subprocess.run([sys.executable, "main.py", "1", "-2", "3"], cwd=WORKSPACE, capture_output=True, text=True, timeout=8)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), expected)
        self.assertEqual(res.stderr, "")

    def test_cli_errors(self):
        for args in ([], ["nope"], ["1.5"]):
            res = subprocess.run([sys.executable, "main.py", *args], cwd=WORKSPACE, capture_output=True, text=True, timeout=8)
            self.assertNotEqual(res.returncode, 0)
            self.assertTrue(res.stderr.strip())
            self.assertNotIn("Traceback", res.stderr)

    def test_documentation_and_test_execution(self):
        for name in ("README.md", "RunningGUIDE.txt"):
            text = (WORKSPACE / name).read_text(encoding="utf-8")
            self.assertIn("python main.py", text)
            self.assertIn("test", text.lower())
        suite = unittest.TestLoader().discover(str(WORKSPACE), pattern="test*.py")
        result = unittest.TestResult()
        suite.run(result)
        self.assertGreaterEqual(result.testsRun, 5)
        self.assertTrue(result.wasSuccessful(), str(result.errors + result.failures))
        self.assertFalse(result.skipped)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
