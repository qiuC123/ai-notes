import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "experiments/project-chemist/reading-order/run.py"
spec = importlib.util.spec_from_file_location("reading_order", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ReadingOrderTests(unittest.TestCase):
    def setUp(self):
        self.raw = b"x = 1\n\ndef target():\n    return x\n"
        self.items = runner.chunks("test.py", self.raw, 1)
        self.sources = {"test.py": self.raw}

    def test_owner_and_exact_match(self):
        self.assertIn("target", self.items[-1]["symbols"])
        self.assertNotIn("tar", self.items[-1]["symbols"])
        packet, used, allocation = runner.select(self.items, ["target"], 100, 20)
        self.assertEqual(packet[0]["lines"][0]["line"], 3)
        self.assertEqual(runner.verify(packet, self.sources, 100)[1], used)
        self.assertLessEqual(allocation["nonmatching_chars"], 20)

    def test_budget_stops_without_partial_line(self):
        packet, used = runner.take(self.items, 5)
        self.assertEqual((packet, used), ([], 0))
        packet, used = runner.take(self.items, 7)
        self.assertEqual(used, 7)
        self.assertEqual(len(packet), 2)

    def test_fallback(self):
        packet, used, allocation = runner.select(self.items, ["missing"], 10, 2)
        self.assertEqual((packet, used), runner.take(self.items, 10))
        self.assertTrue(allocation["fallback"])

    def test_full_range_and_tamper(self):
        packet, _ = runner.take(self.items, 7)
        gold = [{"id": "both", "path": "test.py", "start": 1, "end": 3, "critical": True}]
        result = runner.score(packet, gold, self.sources, 7)
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["facts"][0]["covered_lines"], 2)
        self.assertEqual(result["critical_misses"], ["both"])
        packet[0]["lines"][0]["text"] = "wrong\n"
        with self.assertRaises(AssertionError):
            runner.verify(packet, self.sources, 7)


if __name__ == "__main__":
    unittest.main()
