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
    """Render a speech-led vertical Short with deterministic timing guarantees."""

    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def build(self, script: Script, speech: SpeechResult, clips: list[MediaClip], work_dir: Path, out_path: Path) -> tuple[float, dict]:
        if len(clips) != len(script.scenes):
            raise RuntimeError("Media count must match scene count")

        # Never let the image track decide when the Short ends. The voice is the master
        # clock and we reserve a short visual tail so the last word/CTA can breathe.
        duration = speech.duration + 0.72
        windows = scene_windows(script, speech, duration)
        segments = [self._render_segment(clip, win, work_dir, i) for i, (clip, win) in enumerate(zip(clips, windows))]

        base = work_dir / "base.mp4"
        self._concat(segments, base, work_dir)
        ass = work_dir / "captions.ass"
        self._write_ass(script, speech, windows, duration, ass)
        srt = work_dir / "captions.srt"
        write_srt(speech.words, script.narration, duration, srt)
        self._mux(base, speech.path, ass, duration, work_dir, out_path)

        qa = validate_render(
            out_path,
            self.cfg.width,
            self.cfg.height,
            self.cfg.video_min_seconds,
            self.cfg.video_max_seconds + 3,
            expected_duration=duration,
            min_audio_seconds=speech.duration,
        )
        actual = float(qa["duration"])
        return actual, qa

    def _render_segment(self, clip: MediaClip, win: SceneWindow, work_dir: Path, index: int) -> Path:
        if not clip.local_path:
            raise RuntimeError("Media clip is missing local_path")
        out = work_dir / f"scene_{index+1:02d}.mp4"
        d = win.duration
        scale_w = int(self.cfg.width * 1.08)
        scale_h = int(self.cfg.height * 1.08)
        # Slow crop drift keeps stills/stock alive without fake jump-cuts.
        vf = (
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
            f"crop={self.cfg.width}:{self.cfg.height}:"
            f"x='(in_w-out_w)/2+min((in_w-out_w)/3,12)*sin(t*0.55+{index})':"
            f"y='(in_h-out_h)/2+min((in_h-out_h)/3,16)*cos(t*0.43+{index})',"
            f"fps={self.cfg.fps},"
            "eq=contrast=1.035:saturation=0.92:brightness=-0.025,"
            "unsharp=5:5:0.25:5:5:0.0,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.14:t=fill,"
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

        cta_start = max(windows[-1].start, duration - 4.2)
        for i, (scene, win) in enumerate(zip(script.scenes, windows)):
            if scene.overlay_text:
                start = max(0.08, win.start + (0.15 if i else 2.75))
                end = min(win.end - 0.08, start + 2.0)
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
            cta = f"{exchange}\\NПЕРВАЯ ССЫЛКА В ПРОФИЛЕ\\N{{\\fs{fs_cta_small}}}реферальная ссылка"
        else:
            cta = f"{exchange}\\NFIRST LINK IN PROFILE\\N{{\\fs{fs_cta_small}}}referral link"
        cta_x = self.cfg.width // 2
        cta_y = max(250, round(self.cfg.height * 0.31))
        events.append(f"Dialogue: 5,{ass_time(cta_start)},{ass_time(max(cta_start+0.2, duration-0.08))},CTA,,0,0,0,,{{\\pos({cta_x},{cta_y})\\fad(140,120)}}{cta}")
        path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")

    def _mux(self, base: Path, voice: Path, ass: Path, duration: float, work_dir: Path, out_path: Path) -> None:
        """Mux to an exact speech-led duration.

        The previous implementation used ``-shortest``. If concat rounding or a short
        stock source made the video track shorter than narration, FFmpeg legitimately
        cut the last spoken words. Here both tracks are explicitly padded+trimmed to the
        requested duration, and the render QA rejects any timing drift.
        """
        music = choose_music(self.cfg.music_dir)
        common = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", base.name, "-i", voice.name]
        video_chain = f"[0:v]tpad=stop_mode=clone:stop_duration=8,trim=duration={duration:.3f},setpts=PTS-STARTPTS,ass=captions.ass[v]"
        voice_chain = (
            f"[1:a]highpass=f=75,lowpass=f=14500,acompressor=threshold=-18dB:ratio=2.4:attack=8:release=90,"
            f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[voice]"
        )

        def command(with_music: Path | None) -> list[str]:
            if with_music:
                local_music = work_dir / ("music" + with_music.suffix.lower())
                shutil.copy2(with_music, local_music)
                music_chain = (
                    f"[2:a]volume={self.cfg.music_volume:.4f},apad=whole_dur={duration:.3f},"
                    f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[music]"
                )
                mix = (
                    f"[voice][music]amix=inputs=2:duration=longest:dropout_transition=2,"
                    f"atrim=duration={duration:.3f},loudnorm=I=-15.5:TP=-1.5:LRA=10[a]"
                )
                filter_complex = ";".join([video_chain, voice_chain, music_chain, mix])
                cmd = common + ["-stream_loop", "-1", "-i", local_music.name, "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
            else:
                audio = f"[voice]atrim=duration={duration:.3f},loudnorm=I=-15.5:TP=-1.5:LRA=10[a]"
                filter_complex = ";".join([video_chain, voice_chain, audio])
                cmd = common + ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
            cmd += [
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", self.cfg.video_preset, "-crf", str(self.cfg.video_crf),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path.name,
            ]
            return cmd

        try:
            run(command(music), cwd=work_dir, timeout=600)
        except Exception:
            if not music:
                raise
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
            end_i = total - 1 if i == len(counts) - 1 else min(total - 1, cursor + count - 1)
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


def _norm_token(text: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", str(text).casefold(), flags=re.IGNORECASE)


def _restore_punctuation(words: list[WordTiming], narration: str) -> list[WordTiming]:
    """Map punctuation from narration back onto provider word timings."""
    if not words:
        return []
    original = re.findall(r"\S+", narration)
    decorated: list[WordTiming] = []
    j = 0
    for timed in words:
        target = _norm_token(timed.text)
        chosen = timed.text
        # Sequential fuzzy alignment is enough for Edge/ElevenLabs word streams and
        # avoids losing sentence punctuation that timing providers often omit.
        for k in range(j, min(len(original), j + 5)):
            if _norm_token(original[k]) == target and target:
                chosen = original[k]
                j = k + 1
                break
        decorated.append(WordTiming(chosen, timed.start, timed.end))
    return decorated


def caption_cues(words: list[WordTiming], narration: str, duration: float) -> list[tuple[float, float, str]]:
    if not words:
        raw = re.findall(r"\S+", narration)
        if not raw:
            return []
        approx = duration / len(raw)
        words = [WordTiming(w, i * approx, min(duration, (i + 1) * approx)) for i, w in enumerate(raw)]
    else:
        words = _restore_punctuation(words, narration)

    groups: list[list[WordTiming]] = []
    i = 0
    while i < len(words):
        group: list[WordTiming] = []
        chars = 0
        start = words[i].start
        while i < len(words):
            w = words[i]
            next_chars = chars + (1 if group else 0) + len(w.text)
            elapsed = w.end - start
            numeric_piece = lambda x: bool(re.fullmatch(r"[+\-]?\d[\d.,]*[%$€₽]?", re.sub(r"[,:;.!?]+$", "", x)))
            unit_words = {"доллар", "доллара", "долларов", "рубль", "рубля", "рублей", "usd", "usdt", "eur", "btc", "eth"}
            clean_w = re.sub(r"[^a-zа-яё]", "", w.text.casefold())
            glue_number = bool(group and numeric_piece(group[-1].text) and (numeric_piece(w.text) or clean_w in unit_words))
            # Keep readable semantic phrases rather than a blind 3-word cadence. A
            # thousands-separated number or its currency unit is never split in half.
            if group and not glue_number and (len(group) >= 5 or next_chars > 31 or elapsed > 1.65):
                break
            group.append(w)
            chars = next_chars
            i += 1
            terminal = bool(re.search(r"[.!?…][\"')»]*$", w.text))
            soft = bool(re.search(r"[,;:][\"')»]*$", w.text))
            next_is_number_continuation = bool(i < len(words) and numeric_piece(w.text) and numeric_piece(words[i].text))
            if terminal:
                break
            if soft and len(group) >= 2 and elapsed >= 0.55 and not next_is_number_continuation:
                break
            if len(group) >= 3 and elapsed >= 1.15 and not next_is_number_continuation:
                break
        if group:
            groups.append(group)
        else:
            i += 1

    cues: list[list[float | str]] = []
    for group in groups:
        start = max(0.04, group[0].start - 0.02)
        end = min(duration - 0.02, max(group[-1].end + 0.06, start + 0.28))
        text = " ".join(w.text for w in group).strip()
        cues.append([start, end, text])

    # Enforce strict monotonic, non-overlapping SRT/ASS timing. Provider boundaries
    # may overlap slightly; padding must never make two captions coexist accidentally.
    gap = 0.035
    for idx in range(len(cues)):
        start, end, _ = cues[idx]
        if idx > 0:
            prev_end = float(cues[idx - 1][1])
            start = max(float(start), prev_end + gap)
        if idx + 1 < len(cues):
            next_start = float(cues[idx + 1][0])
            end = min(float(end), next_start - gap)
        if end <= start + 0.12:
            end = min(duration - 0.01, start + 0.18)
        cues[idx][0], cues[idx][1] = start, end

    # One final pass because extending a very short cue can touch the following cue.
    for idx in range(len(cues) - 1):
        max_end = float(cues[idx + 1][0]) - gap
        cues[idx][1] = min(float(cues[idx][1]), max_end)
    return [(float(a), float(b), str(t)) for a, b, t in cues if float(b) > float(a)]


def write_srt(words: list[WordTiming], narration: str, duration: float, path: Path) -> None:
    cues = caption_cues(words, narration, duration)
    lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.extend([str(i), f"{srt_time(start)} --> {srt_time(end)}", text, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def ass_wrap(text: str, max_chars: int, max_lines: int = 2) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    max_chars = max(4, max_chars)
    words: list[str] = []
    for raw in clean.split(" "):
        if len(raw) <= max_chars:
            words.append(raw)
        else:
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
