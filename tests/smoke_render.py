"""Small FFmpeg integration smoke test. No network or API keys required."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import tempfile

from config import Settings
from media import MediaProvider
from models import Scene, Script, SpeechResult, WordTiming
from utils import run
from video_builder import VideoBuilder


def main():
    cfg = Settings(width=360, height=640, fps=15, video_min_seconds=3, video_max_seconds=10, video_preset="veryfast")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        scenes = [Scene("One two three four.", "crypto chart phone", "FIRST"), Scene("Five six seven eight.", "cyber security phone", "SECOND")]
        script = Script("test", "TEST HOOK", scenes, "Test", "Test", ["shorts"], "test")
        provider = MediaProvider(cfg)
        clips = [provider._generated(s, root, i) for i, s in enumerate(scenes)]
        voice = root / "voice.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "5", str(voice)])
        words = [WordTiming(t, i*.55, i*.55+.4) for i, t in enumerate("One two three four Five six seven eight".split())]
        speech = SpeechResult(voice, "test", 5.0, words)
        out = root / "smoke.mp4"
        _, qa = VideoBuilder(cfg).build(script, speech, clips, root, out)
        assert out.exists() and out.stat().st_size > 50_000
        assert qa["width"] == 360 and qa["height"] == 640
        # Regression guard for the real Actions failure where a 53.2s voice became
        # a 51.5s MP4 because the visual track ended first.
        assert qa["duration"] >= speech.duration + 0.65
        assert qa["audio_duration"] >= speech.duration
        print("SMOKE_RENDER_OK", qa)


if __name__ == "__main__":
    main()
