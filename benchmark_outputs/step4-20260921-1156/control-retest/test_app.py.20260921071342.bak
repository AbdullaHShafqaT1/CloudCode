
import unittest
from app_logic import summarize

class TestSummarize(unittest.TestCase):
    def test_boolean(self):
        with self.assertRaises(TypeError):
            summarize([True, False])
    
    def test_empty(self):
        with self.assertRaises(ValueError):
            summarize([])
    
    def test_float(self):
        with self.assertRaises(TypeError):
            summarize([1.5, 2.5])
    
    def test_invalid_container(self):
        with self.assertRaises(TypeError):
            summarize("not a list")
    
    def test_large_integer(self):
        large_int = 10**10
        result = summarize([large_int])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['total'], large_int)
        self.assertEqual(result['mean'], large_int)
        self.assertEqual(result['minimum'], large_int)
        self.assertEqual(result['maximum'], large_int)
    
    def test_negative(self):
        result = summarize([-1, -2, -3])
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['total'], -6)
        self.assertEqual(result['mean'], -2)
        self.assertEqual(result['minimum'], -3)
        self.assertEqual(result['maximum'], -1)
    
    def test_normal(self):
        result = summarize([1, 2, 3])
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['total'], 6)
        self.assertEqual(result['mean'], 2)
        self.assertEqual(result['minimum'], 1)
        self.assertEqual(result['maximum'], 3)
    
    def test_singleton(self):
        result = summarize([42])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['total'], 42)
        self.assertEqual(result['mean'], 42)
        self.assertEqual(result['minimum'], 42)
        self.assertEqual(result['maximum'], 42)
    
    def test_tuple(self):
        result = summarize((1, 2, 3))
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['total'], 6)
        self.assertEqual(result['mean'], 2)
        self.assertEqual(result['minimum'], 1)
        self.assertEqual(result['maximum'], 3)
    
    def test_string(self):
        with self.assertRaises(TypeError):
            summarize(["a", "b", "c"])

if __name__ == "__main__":
    unittest.main()