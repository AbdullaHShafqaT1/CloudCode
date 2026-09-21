
import unittest
from app_logic import summarize

class TestSummarize(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(summarize([1, 2, 3]), {"count": 3, "total": 6, "mean": 2.0, "minimum": 1, "maximum": 3})
    
    def test_negative(self):
        self.assertEqual(summarize([-1, -2, -3]), {"count": 3, "total": -6, "mean": -2.0, "minimum": -3, "maximum": -1})
    
    def test_singleton(self):
        self.assertEqual(summarize([42]), {"count": 1, "total": 42, "mean": 42.0, "minimum": 42, "maximum": 42})
    
    def test_tuple(self):
        self.assertEqual(summarize((1, 2, 3)), {"count": 3, "total": 6, "mean": 2.0, "minimum": 1, "maximum": 3})
    
    def test_large_integer(self):
        self.assertEqual(summarize([10**10, 10**10]), {"count": 2, "total": 20000000000, "mean": 10000000000.0, "minimum": 10000000000, "maximum": 10000000000})
    
    def test_empty(self):
        with self.assertRaises(ValueError):
            summarize([])
    
    def test_boolean(self):
        with self.assertRaises(TypeError):
            summarize([True, False])
    
    def test_float(self):
        with self.assertRaises(TypeError):
            summarize([1.5, 2.5])
    
    def test_string(self):
        with self.assertRaises(TypeError):
            summarize(["a", "b"])
    
    def test_invalid_container(self):
        with self.assertRaises(TypeError):
            summarize({"a": 1, "b": 2})

if __name__ == "__main__":
    unittest.main()