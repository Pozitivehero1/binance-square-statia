from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from config import Settings
from models import MediaClip, Script, SpeechResult, WordTiming
from utils import LOG, run, validate_render


@dataclass
class SceneWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.05, self.end - self.start)


class VideoBuilder:
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def build(self, script: Script, speech: SpeechResult, clips: list[MediaClip], work_dir: Path, out_path: Path) -> tuple[float, dict]:
        if len(clips) != len(script.scenes):
            raise RuntimeError("Media count must match scene count")
        duration = speech.duration + 0.28
        windows = scene_windows(script, speech, duration)
        segments = [self._render_segment(clip, win, work_dir, i) for i, (clip, win) in enumerate(zip(clips, windows))]
        base = work_dir / "base.mp4"
        self._concat(segments, base, work_dir)
        ass = work_dir / "captions.ass"
        self._write_ass(script, speech, windows, duration, ass)
        srt = work_dir / "captions.srt"
        write_srt(speech.words, script.narration, duration, srt)
        self._mux(base, speech.path, ass, duration, work_dir, out_path)
        qa = validate_render(out_path, self.cfg.width, self.cfg.height, self.cfg.video_min_seconds, self.cfg.video_max_seconds)
        return duration, qa

    def _render_segment(self, clip: MediaClip, win: SceneWindow, work_dir: Path, index: int) -> Path:
        if not clip.local_path:
            raise RuntimeError("Media clip is missing local_path")
        out = work_dir / f"scene_{index+1:02d}.mp4"
        d = win.duration
        scale_w = int(self.cfg.width * 1.08)
        scale_h = int(self.cfg.height * 1.08)
        # Slow crop drift keeps stock footage alive without artificial jump cuts.
        vf = (
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
            f"crop={self.cfg.width}:{self.cfg.height}:"
            f"x='(in_w-out_w)/2+min((in_w-out_w)/3,12)*sin(t*0.55+{index})':"
            f"y='(in_h-out_h)/2+min((in_h-out_h)/3,16)*cos(t*0.43+{index})',"
            f"fps={self.cfg.fps},"
            "eq=contrast=1.035:saturation=0.92:brightness=-0.025,"
            "unsharp=5:5:0.25:5:5:0.0,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.16:t=fill,"
            "fade=t=in:st=0:d=0.08"
        )
        if clip.is_image:
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(clip.local_path)]
        else:
            source_d = max(clip.duration, d + 0.2)
            max_start = max(0.0, source_d - d - 0.2)
            start = random.uniform(0, max_start) if max_start > 0.8 else 0.0
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", str(clip.local_path)]
        cmd += [
            "-t", f"{d:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(self.cfg.fps), "-movflags", "+faststart", str(out),
        ]
        run(cmd)
        return out

    def _concat(self, segments: list[Path], out: Path, work_dir: Path) -> None:
        file = work_dir / "concat.txt"
        file.write_text("\n".join(f"file '{p.name}'" for p in segments) + "\n", encoding="utf-8")
        # Re-encode the concat once to normalize timestamps and avoid concat-demuxer edge cases.
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", file.name,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(self.cfg.fps), out.name,
        ], cwd=work_dir)

    def _write_ass(self, script: Script, speech: SpeechResult, windows: list[SceneWindow], duration: float, path: Path) -> None:
        font = "DejaVu Sans"
        scale = max(0.34, self.cfg.width / 1080.0)
        fs_caption = max(24, round(70 * scale))
        fs_hook = max(28, round(84 * scale))
        fs_beat = max(22, round(58 * scale))
        fs_cta = max(24, round(70 * scale))
        fs_cta_small = max(16, round(38 * scale))
        outline = max(2, round(5 * scale))
        margin_x = max(28, round(88 * scale))
        hook_margin = max(28, round(78 * scale))
        caption_v = max(110, round(335 * scale))
        hook_v = max(82, round(235 * scale))
        beat_v = max(120, round(360 * scale))
        cta_v = max(200, round(600 * scale))

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.cfg.width}
PlayResY: {self.cfg.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,{font},{fs_caption},&H00FFFFFF,&H002BE8FF,&H00101010,&H50000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,{margin_x},{margin_x},{caption_v},1
Style: Hook,{font},{fs_hook},&H002BE8FF,&H00FFFFFF,&H00101010,&H60000000,-1,0,0,0,100,100,0,0,1,{outline+1},1,8,{hook_margin},{hook_margin},{hook_v},1
Style: Beat,{font},{fs_beat},&H00FFFFFF,&H002BE8FF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,3,2,0,8,{margin_x},{margin_x},{beat_v},1
Style: CTA,{font},{fs_cta},&H00FFFFFF,&H002BE8FF,&H00101010,&HC0000000,-1,0,0,0,100,100,0,0,3,2,0,5,{margin_x},{margin_x},{cta_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
        events: list[str] = []
        hook_end = min(2.7, duration)
        hook_text = ass_wrap(script.hook.upper(), 17, max_lines=4)
        events.append(f"Dialogue: 4,{ass_time(0.05)},{ass_time(hook_end)},Hook,,0,0,0,,{{\\fscx92\\fscy92\\t(0,160,\\fscx100\\fscy100)}}{hook_text}")

        cta_start = max(windows[-1].start, duration - 3.8)
        for i, (scene, win) in enumerate(zip(script.scenes, windows)):
            if scene.overlay_text:
                start = max(0.08, win.start + (0.15 if i else 2.75))
                end = min(win.end - 0.08, start + 2.1)
                if i == len(script.scenes) - 1:
                    end = min(end, cta_start - 0.12)
                if end > start + 0.2:
                    beat = ass_wrap(scene.overlay_text.upper(), 22, max_lines=2)
                    events.append(f"Dialogue: 3,{ass_time(start)},{ass_time(end)},Beat,,0,0,0,,{{\\fad(90,120)}}{beat}")

        cues = caption_cues(speech.words, script.narration, duration)
        for start, end, text in cues:
            if start >= cta_start:
                continue
            end = min(end, cta_start - 0.04)
            if end <= start + 0.12:
                continue
            caption = ass_wrap(text, 24, max_lines=2)
            events.append(
                f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
                f"{{\\fscx94\\fscy94\\t(0,100,\\fscx100\\fscy100)}}{caption}"
            )

        exchange = ass_wrap(self.cfg.exchange_name.upper(), 20, max_lines=2)
        if self.cfg.language == "ru":
            cta = f"{exchange}\\NССЫЛКА В ПРОФИЛЕ\\N{{\\fs{fs_cta_small}}}реферальная ссылка"
        else:
            cta = f"{exchange}\\NLINK IN PROFILE\\N{{\\fs{fs_cta_small}}}referral link"
        events.append(f"Dialogue: 5,{ass_time(cta_start)},{ass_time(max(cta_start+0.2, duration-0.08))},CTA,,0,0,0,,{{\\fad(140,120)}}{cta}")
        path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")

    def _mux(self, base: Path, voice: Path, ass: Path, duration: float, work_dir: Path, out_path: Path) -> None:
        music = choose_music(self.cfg.music_dir)
        common = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", base.name, "-i", voice.name]

        def command(with_music: Path | None) -> list[str]:
            if with_music:
                local_music = work_dir / ("music" + with_music.suffix.lower())
                shutil.copy2(with_music, local_music)
                filter_complex = (
                    "[0:v]ass=captions.ass[v];"
                    "[1:a]highpass=f=75,lowpass=f=14500,acompressor=threshold=-18dB:ratio=2.4:attack=8:release=90,apad=pad_dur=0.5[voice];"
                    f"[2:a]volume={self.cfg.music_volume:.4f}[music];"
                    "[voice][music]amix=inputs=2:duration=first:dropout_transition=2,"
                    "loudnorm=I=-15.5:TP=-1.5:LRA=10[a]"
                )
                cmd = common + ["-stream_loop", "-1", "-i", local_music.name, "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
            else:
                filter_complex = (
                    "[0:v]ass=captions.ass[v];"
                    "[1:a]highpass=f=75,lowpass=f=14500,acompressor=threshold=-18dB:ratio=2.4:attack=8:release=90,apad=pad_dur=0.5,"
                    "loudnorm=I=-15.5:TP=-1.5:LRA=10[a]"
                )
                cmd = common + ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
            cmd += [
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", self.cfg.video_preset, "-crf", str(self.cfg.video_crf),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", out_path.name,
            ]
            return cmd

        try:
            run(command(music), cwd=work_dir, timeout=600)
        except Exception:
            if not music:
                raise
            # A bad/unreadable optional music file must never destroy an otherwise valid Short.
            LOG.exception("Music mix failed for %s; retrying final mux without music", music.name)
            run(command(None), cwd=work_dir, timeout=600)

        rendered = work_dir / out_path.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if rendered.resolve() != out_path.resolve():
            shutil.move(str(rendered), str(out_path))
        LOG.info("Rendered %s", out_path)


def scene_windows(script: Script, speech: SpeechResult, duration: float) -> list[SceneWindow]:
    counts = [max(1, len(re.findall(r"\b[\w'-]+\b", s.voiceover, flags=re.UNICODE))) for s in script.scenes]
    if speech.words and len(speech.words) >= sum(counts) * 0.72:
        windows: list[SceneWindow] = []
        cursor = 0
        total = len(speech.words)
        for i, count in enumerate(counts):
            start_i = min(cursor, total - 1)
            if i == len(counts) - 1:
                end_i = total - 1
            else:
                end_i = min(total - 1, cursor + count - 1)
            start = 0.0 if i == 0 else max(0.0, speech.words[start_i].start - 0.06)
            end = duration if i == len(counts) - 1 else min(duration, speech.words[end_i].end + 0.12)
            if windows and start < windows[-1].end:
                mid = (start + windows[-1].end) / 2
                windows[-1].end = mid
                start = mid
            windows.append(SceneWindow(start, max(start + 0.35, end)))
            cursor += count
        windows[-1].end = duration
        return windows
    total_weight = sum(counts)
    cursor = 0.0
    windows = []
    for i, weight in enumerate(counts):
        end = duration if i == len(counts) - 1 else cursor + duration * weight / total_weight
        windows.append(SceneWindow(cursor, end))
        cursor = end
    return windows


def caption_cues(words: list[WordTiming], narration: str, duration: float) -> list[tuple[float, float, str]]:
    if not words:
        raw = re.findall(r"\S+", narration)
        if not raw:
            return []
        approx = duration / len(raw)
        words = [WordTiming(w, i * approx, min(duration, (i + 1) * approx)) for i, w in enumerate(raw)]
    cues: list[tuple[float, float, str]] = []
    i = 0
    while i < len(words):
        # 2-5 words, keeping on-screen time around 0.75-1.6s.
        j = min(len(words), i + 3)
        while j < min(len(words), i + 5) and words[j-1].end - words[i].start < 0.78:
            j += 1
        while j > i + 2 and words[j-1].end - words[i].start > 1.75:
            j -= 1
        group = words[i:j]
        text = " ".join(w.text for w in group).strip()
        start = max(0.05, group[0].start - 0.035)
        end = min(duration - 0.03, max(group[-1].end + 0.10, start + 0.35))
        cues.append((start, end, text))
        i = j
    return cues


def write_srt(words: list[WordTiming], narration: str, duration: float, path: Path) -> None:
    cues = caption_cues(words, narration, duration)
    lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.extend([str(i), f"{srt_time(start)} --> {srt_time(end)}", text, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def ass_wrap(text: str, max_chars: int, max_lines: int = 2) -> str:
    """Wrap ASS text without ever creating a line wider than ``max_chars``.

    ``max_lines`` is intentionally a soft vertical target. If squeezing text into that
    many lines would make a line too wide, we keep the extra wrapped line instead of
    risking horizontal clipping on Shorts.
    """
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    if max_chars < 4:
        max_chars = 4

    # Split pathological long tokens (URLs/tickers/etc.) before normal word wrapping.
    words: list[str] = []
    for raw in clean.split(" "):
        if len(raw) <= max_chars:
            words.append(raw)
            continue
        words.extend(raw[i : i + max_chars] for i in range(0, len(raw), max_chars))

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    # Never merge overflow lines: horizontal readability is more important than
    # respecting the soft line target. Current call sites also validate hook length.
    _ = max_lines
    return r"\N".join(ass_escape(x) for x in lines)


def ass_escape(text: str) -> str:
    return str(text).replace("{", "(").replace("}", ")").replace("\n", " ").replace("\\", "/").strip()


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def srt_time(seconds: float) -> str:
    ms = int(max(0.0, seconds) * 1000 + 0.5)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def choose_music(music_dir: Path) -> Path | None:
    if not music_dir.exists():
        return None
    files = [p for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac"}]
    return random.choice(files) if files else None
