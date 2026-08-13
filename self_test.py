from __future__ import annotations

import tempfile
from pathlib import Path

import generate_video as g


def make_plan(words_per_scene: int = 10, scenes: int = 14):
    narration = " ".join(["science"] * words_per_scene)
    return {
        "title": "Test topic",
        "hook": "A specific factual hook.",
        "caption": "Test",
        "scenes": [
            {"narration": narration, "pexels_queries": ["science laboratory"]}
            for _ in range(scenes)
        ],
    }


def main() -> None:
    # Caption layout regression test.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "captions.srt"
        p.write_text(
            "1\n00:00:00,000 --> 00:00:06,000\nThis intentionally long subtitle sentence should be split into smaller readable TikTok caption chunks.\n\n",
            encoding="utf-8",
        )
        g.compact_captions(p, max_words=6, max_chars=34)
        entries = g.parse_srt_entries(p)
        assert len(entries) >= 2
        assert all(len(text.split()) <= 8 for _, _, text in entries)
        ass = Path(td) / "captions.ass"
        g.make_ass_from_srt(p, ass)
        txt = ass.read_text(encoding="utf-8")
        assert "PlayResX: 1080" in txt and "PlayResY: 1920" in txt
        assert "Style: TikTok" in txt

    # Length repair must keep the best usable candidate instead of failing just because
    # Mistral misses an exact requested count.
    original = g.mistral_json
    try:
        attempts = iter([make_plan(11, 14), make_plan(13, 15)])  # 154 words, then 195 words
        g.mistral_json = lambda *a, **k: next(attempts)
        repaired = g.rebalance_plan_length(
            {"title": "Test", "search": "test"},
            "Mistral conservative fact brief",
            "FACT 1: test. " * 100,
            make_plan(9, 14),  # 126 words
        )
        wc = g.words(" ".join(x["narration"] for x in repaired["scenes"]))
        assert 175 <= wc <= 225, wc
    finally:
        g.mistral_json = original


    # A single Wikimedia 429 disables Wikipedia for the rest of the run; later topic attempts
    # must not hit the shared GitHub runner IP again.
    original_get = g.requests.get
    calls = {"n": 0}
    class Fake429:
        status_code = 429
        headers = {"Retry-After": "30"}
    def fake_get(*a, **k):
        calls["n"] += 1
        return Fake429()
    try:
        g.WIKIPEDIA_DISABLED_THIS_RUN = False
        g.requests.get = fake_get
        try:
            g.wikipedia_get({"action": "query"})
        except RuntimeError:
            pass
        first_calls = calls["n"]
        try:
            g.wikipedia_get({"action": "query"})
        except RuntimeError:
            pass
        assert first_calls == 1 and calls["n"] == 1
    finally:
        g.requests.get = original_get
        g.WIKIPEDIA_DISABLED_THIS_RUN = False

    # QA wording/retention complaints are polish, not fatal factual errors.
    fatal, polish = g._demote_obvious_polish_from_fatal(
        ["Weak first sentence", "Unsupported claim about a date"], []
    )
    assert fatal == ["Unsupported claim about a date"]
    assert "Weak first sentence" in polish

    # Short scripts get a naturally slower TTS rate before any FFmpeg stretch.
    assert g.voice_rate_for_word_count(135) == "-15%"
    assert g.voice_rate_for_word_count(150) == "-10%"

    print("SELF-TEST: OK")


if __name__ == "__main__":
    main()
