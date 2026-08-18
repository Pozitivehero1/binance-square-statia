import tempfile
import unittest
import requests
from pathlib import Path

from models import Script, Scene, SpeechResult, WordTiming, Topic
from tts import alignment_to_words
from script_generator import ScriptGenerator
from utils import request_with_retry, similarity
from video_builder import ass_wrap, caption_cues, scene_windows


class CoreTests(unittest.TestCase):
    def test_similarity(self):
        self.assertGreater(similarity("Почему плечо опасно новичкам", "Почему плечо опасно для новичка"), 0.25)
        self.assertLess(similarity("funding rate", "seed phrase security"), 0.2)

    def test_alignment_to_words(self):
        a = {
            "characters": list("Hi all"),
            "character_start_times_seconds": [0, .1, .2, .3, .4, .5],
            "character_end_times_seconds": [.1, .2, .3, .4, .5, .6],
        }
        w = alignment_to_words(a)
        self.assertEqual([x.text for x in w], ["Hi", "all"])
        self.assertAlmostEqual(w[1].end, .6)

    def test_caption_cues(self):
        words = [WordTiming(f"w{i}", i * .3, i * .3 + .25) for i in range(12)]
        cues = caption_cues(words, "", 4)
        self.assertGreaterEqual(len(cues), 3)
        self.assertTrue(all(a < b for a, b, _ in cues))

    def test_scene_windows(self):
        script = Script("t", "h", [Scene("one two three", "q", "x"), Scene("four five six", "q2", "y")], "title", "desc", ["shorts"], "src")
        speech = SpeechResult(Path("x"), "test", 4.0, [WordTiming(x, i*.5, i*.5+.4) for i, x in enumerate("one two three four five six".split())])
        windows = scene_windows(script, speech, 4.2)
        self.assertEqual(len(windows), 2)
        self.assertAlmostEqual(windows[-1].end, 4.2)
        self.assertGreater(windows[1].start, 0)

    def test_ass_wrap(self):
        wrapped = ass_wrap("очень длинный заголовок который должен переноситься аккуратно", 18, 3)
        self.assertIn(r"\N", wrapped)
        self.assertTrue(all(len(line) <= 18 for line in wrapped.split(r"\N")))
        pathological = ass_wrap("SUPERCALIFRAGILISTICEXPIALIDOCIOUS tail", 10, 2)
        self.assertTrue(all(len(line) <= 10 for line in pathological.split(r"\N")))

    def test_nonretryable_http_error_is_immediate(self):
        class FakeResponse:
            status_code = 400
            headers = {}
            def raise_for_status(self):
                response = requests.Response(); response.status_code = 400
                raise requests.HTTPError("bad request", response=response)
        class FakeSession:
            def __init__(self): self.calls = 0
            def request(self, *args, **kwargs): self.calls += 1; return FakeResponse()
        session = FakeSession()
        with self.assertRaises(requests.HTTPError):
            request_with_retry(session, "POST", "https://example.invalid", retries=4)
        self.assertEqual(session.calls, 1)

    def test_market_percentage_guard(self):
        topic = Topic(
            "BTC move", "facts", "q", "fp", "CoinGecko /coins/markets", kind="market",
            data={"change_1h": 1.23, "change_24h": -4.72, "change_7d": 9.81},
        )
        ScriptGenerator._validate_market_percentages("BTC снизился на 4,7% за 24 часа.", topic)
        ScriptGenerator._validate_market_percentages("За 1 час BTC вырос на 1,2%.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_percentages("BTC вырос на 4,7% за 24 часа.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_percentages("За 1 час BTC снизился на 4,7%.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_percentages("BTC вырос на 18%.", topic)

    def test_market_money_guard(self):
        topic = Topic(
            "BTC move", "facts", "q", "fp", "CoinGecko /coins/markets", kind="market",
            data={"price": 105250.0, "high_24h": 107100.0, "low_24h": 102500.0, "volume_24h": 2.4e9, "market_cap": 2.1e12},
        )
        ScriptGenerator._validate_market_money_claims("Цена около $105,250. Объём $2.4B.", topic)
        ScriptGenerator._validate_market_money_claims("Максимум за 24 часа $107,100.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_money_claims("Минимум за 24 часа $107,100.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_money_claims("Цена будет $150,000.", topic)


if __name__ == "__main__":
    unittest.main()
