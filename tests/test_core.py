import tempfile
import unittest
import requests
from pathlib import Path

from models import Script, Scene, SpeechResult, WordTiming, Topic
from tts import alignment_to_words
from script_generator import ScriptGenerator
from topic_source import TopicSource
from media import MediaProvider
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
            "BTC move", "facts", "q", "fp", "Binance Spot public market data /api/v3/ticker/24hr", kind="market",
            data={"change_24h": -4.72},
        )
        ScriptGenerator._validate_market_percentages("BTC снизился на 4,7% за 24 часа.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_percentages("BTC вырос на 4,7% за 24 часа.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_percentages("BTC вырос на 18%.", topic)

    def test_market_money_guard(self):
        topic = Topic(
            "BTC move", "facts", "q", "fp", "Binance Spot public market data /api/v3/ticker/24hr", kind="market",
            data={"price": 105250.0, "high_24h": 107100.0, "low_24h": 102500.0, "volume_24h": 2.4e9},
        )
        ScriptGenerator._validate_market_money_claims("Цена около $105,250. Объём $2.4B.", topic)
        ScriptGenerator._validate_market_money_claims("Максимум за 24 часа $107,100.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_money_claims("Минимум за 24 часа $107,100.", topic)
        with self.assertRaises(ValueError):
            ScriptGenerator._validate_market_money_claims("Цена будет $150,000.", topic)

    def test_binance_market_row_ranking(self):
        rows = [
            {"symbol":"BTCUSDT","lastPrice":"65000","priceChangePercent":"-4.2","highPrice":"67000","lowPrice":"64000","quoteVolume":"900000000","weightedAvgPrice":"65500","count":1200000},
            {"symbol":"USDCUSDT","lastPrice":"1","priceChangePercent":"2.0","highPrice":"1.01","lowPrice":"0.99","quoteVolume":"50000000","weightedAvgPrice":"1","count":10000},
            {"symbol":"TINYUSDT","lastPrice":"0.1","priceChangePercent":"20","highPrice":"0.12","lowPrice":"0.08","quoteVolume":"1000","weightedAvgPrice":"0.1","count":50},
        ]
        ranked = TopicSource._rank_binance_rows(rows)
        self.assertEqual(len(ranked), 1)
        item = ranked[0][1]
        self.assertEqual(item["symbol"], "BTCUSDT")
        self.assertEqual(item["quote_asset"], "USDT")
        self.assertAlmostEqual(item["change_24h"], -4.2)

    def test_caption_cues_preserve_punctuation_and_never_overlap(self):
        narration = "Спред — это разница. Чем он меньше, тем обычно уже рынок."
        tokens = "Спред это разница Чем он меньше тем обычно уже рынок".split()
        words = [WordTiming(t, i * .42, i * .42 + .34) for i, t in enumerate(tokens)]
        cues = caption_cues(words, narration, 5.5)
        joined = " ".join(t for _, _, t in cues)
        self.assertIn("разница.", joined)
        self.assertIn("меньше,", joined)
        for left, right in zip(cues, cues[1:]):
            self.assertLessEqual(left[1], right[0])
        self.assertFalse(any("разница. Чем" in text for _, _, text in cues))

    def test_media_relevance_rejects_unrelated_lifestyle_slug(self):
        from models import MediaClip
        bad = MediaClip(1, "trader smartphone market app", pexels_url="https://www.pexels.com/video/person-browsing-clothes-on-laptop-5586010/")
        good = MediaClip(2, "trader smartphone market app", pexels_url="https://www.pexels.com/video/monitoring-the-stock-market-7947488/")
        self.assertLess(MediaProvider._relevance(bad, bad.query), 0.42)
        self.assertGreaterEqual(MediaProvider._relevance(good, good.query), 0.42)

    def test_graphic_routing_for_exact_numbers_and_cta(self):
        scenes = [
            Scene("Бид 50 000, аск 50 100. Спред 100 долларов.", "trading numbers screen", "СПРЕД"),
            Scene("Хочешь посмотреть Binance? Первая ссылка — в профиле канала.", "trader smartphone app", "BINANCE"),
        ]
        self.assertEqual(MediaProvider._graphic_kind(scenes[0], 0, 2), "orderbook")
        self.assertEqual(MediaProvider._graphic_kind(scenes[1], 1, 2), "cta")

    def test_neutral_cta_guard(self):
        from config import Settings
        cfg = Settings(language="ru")
        gen = ScriptGenerator(cfg)
        topic = Topic("Спред", "facts", "q", "fp", "curated", kind="evergreen")
        base = [
            Scene("Спред — разница между лучшей ценой покупки и продажи.", "trading market smartphone", "СПРЕД", "graphic"),
            Scene("Он может быть уже или шире в разных условиях, поэтому перед сделкой полезно смотреть обе стороны цены.", "financial market tablet", "РАЗНИЦА", "stock"),
            Scene("Это не отдельная комиссия биржи: спред возникает как разница между лучшей заявкой на покупку и лучшей заявкой на продажу.", "trader hands smartphone", "НЕ КОМИССИЯ", "graphic"),
            Scene("Для крупных ордеров важна и глубина стакана, потому что одна лучшая цена не показывает весь доступный объём рядом.", "trading desk tablet", "ГЛУБИНА", "stock"),
            Scene("Проверяй цену до подтверждения. Хочешь посмотреть Binance? Первая ссылка — в профиле канала.", "trader smartphone finance", "ПРОФИЛЬ", "graphic"),
        ]
        script = Script("t", "Спред — это разница?", base, "Что такое спред", "Коротко о механике спреда.", ["crypto","trading","spread","education","shorts"], "curated")
        gen._validate(script, topic)
        bad = list(base)
        bad[-1] = Scene("На Binance низкие спреды. Хочешь посмотреть Binance? Первая ссылка — в профиле канала.", "trader smartphone finance", "ПРОФИЛЬ", "graphic")
        bad_script = Script("t", "Спред — это разница?", bad, "Что такое спред", "Коротко о механике спреда.", ["crypto","trading","spread","education","shorts"], "curated")
        with self.assertRaises(ValueError):
            gen._validate(bad_script, topic)

    def test_model_cta_and_query_are_sanitized(self):
        from config import Settings
        gen = ScriptGenerator(Settings(language="ru"))
        text = "Чтобы торговать с низкими спредами, переходи по ссылке Binance в профиле канала."
        normalized = gen._normalize_final_voice(text)
        self.assertEqual(normalized, "Хочешь посмотреть Binance? Первая ссылка — в профиле канала.")
        q = gen._normalize_visual_query("split-screen Binance app showing 50,000 highlighted BID ASK animation", "graphic")
        self.assertNotIn("binance", q)
        self.assertNotIn("50000", q.replace(" ", ""))
        self.assertNotIn("animation", q)


if __name__ == "__main__":
    unittest.main()
