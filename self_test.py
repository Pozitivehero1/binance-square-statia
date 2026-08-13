from __future__ import annotations

import tempfile
from pathlib import Path

import generate_video as g


def main() -> None:
    assert "disambiguation" not in [x["title"].lower() for x in []]
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
    print("SELF-TEST: OK")


if __name__ == "__main__":
    main()
