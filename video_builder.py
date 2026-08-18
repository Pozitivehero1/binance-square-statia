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
        scale_w = int(self.cfg.width * 1.16)
        scale_h = int(self.cfg.height * 1.16)
        phase = (index * 0.91) % 6.28
        darken = 0.13 if clip.source == "pexels" else 0.06
        vf = (
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
            f"crop={self.cfg.width}:{self.cfg.height}:"
            f"x='(in_w-out_w)/2+(in_w-out_w)*0.18*sin(t*0.42+{phase:.2f})':"
            f"y='(in_h-out_h)/2+(in_h-out_h)*0.16*cos(t*0.36+{phase:.2f})',"
            f"fps={self.cfg.fps},"
            "eq=contrast=1.07:saturation=1.03:brightness=-0.025,"
            "unsharp=5:5:0.20:5:5:0.0,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{darken}:t=fill,"
            "drawbox=x=0:y=0:w=iw:h=260:color=black@0.16:t=fill,"
            "drawbox=x=0:y=1450:w=iw:h=470:color=black@0.23:t=fill,"
            f"fade=t=in:st=0:d={min(0.11, d/6):.3f},"
            f"fade=t=out:st={max(0.0, d-0.10):.3f}:d=0.10"
        )
        if clip.is_image:
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(clip.local_path)]
        else:
            source_d = max(clip.duration, d + 0.2)
            max_start = max(0.0, source_d - d - 0.2)
            start = random.uniform(0, max_start) if max_start > 0.8 else 0.0
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", str(clip.local_path)]
        cmd += [
            "-t", f"{d:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
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
        fs_caption = max(24, round(62 * scale))
        fs_hook = max(34, round(92 * scale))
        fs_beat = max(34, round(112 * scale))
        fs_cta = max(34, round(86 * scale))
        fs_cta_small = max(18, round(36 * scale))
        outline = max(2, round(5 * scale))
        margin_x = max(30, round(84 * scale))
        caption_v = max(105, round(255 * scale))

        white = "&H00FFFFFF"
        accent = "&H004BC7F8"
        red = "&H00745BFF"
        green = "&H00A5D747"
        shadow = "&H78000000"

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.cfg.width}
PlayResY: {self.cfg.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,{font},{fs_caption},{white},{accent},&H00101010,{shadow},-1,0,0,0,100,100,0,0,1,{outline},1,2,{margin_x},{margin_x},{caption_v},1
Style: Hook,{font},{fs_hook},{white},{accent},&H00101010,&H00000000,-1,0,0,0,100,100,-1,0,1,{outline+1},2,5,{margin_x},{margin_x},0,1
Style: Beat,{font},{fs_beat},{accent},{white},&H00101010,&H00000000,-1,0,0,0,100,100,-1,0,1,{outline},2,5,{margin_x},{margin_x},0,1
Style: CTA,{font},{fs_cta},{white},{accent},&H00101010,&H00000000,-1,0,0,0,100,100,-1,0,1,{outline},2,5,{margin_x},{margin_x},0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
        events: list[str] = []
        hook_end = min(2.55, duration)
        hook_text = ass_wrap(script.hook.upper(), 18, max_lines=3)
        events.append(
            f"Dialogue: 5,{ass_time(0.04)},{ass_time(hook_end)},Hook,,0,0,0,,"
            f"{{\\pos({self.cfg.width//2},{round(self.cfg.height*0.31)})\\fscx88\\fscy88\\blur0.4"
            f"\\t(0,180,\\fscx102\\fscy102)\\t(180,320,\\fscx100\\fscy100)\\fad(80,120)}}{hook_text}"
        )

        cta_start = max(windows[-1].start, duration - 4.3)
        for i, (scene, win) in enumerate(zip(script.scenes, windows)):
            if i == len(script.scenes) - 1 or not scene.overlay_text:
                continue
            start = max(win.start + 0.12, 2.65 if i == 0 else win.start + 0.12)
            end = min(win.end - 0.12, start + min(1.75, max(0.85, win.duration * 0.34)))
            if end <= start + 0.2:
                continue
            beat = ass_wrap(scene.overlay_text.upper(), 16, max_lines=2)
            low = scene.overlay_text.lower()
            color = accent
            if re.search(r"(?:^|\s)-\s*\d|пад|сниз|down|drop|fell|loss", low):
                color = red
            elif re.search(r"(?:^|\s)\+\s*\d|рост|вырос|up|gain|rose", low):
                color = green
            y = round(self.cfg.height * (0.43 if i % 2 == 0 else 0.39))
            events.append(
                f"Dialogue: 4,{ass_time(start)},{ass_time(end)},Beat,,0,0,0,,"
                f"{{\\pos({self.cfg.width//2},{y})\\1c{color}\\fscx84\\fscy84\\blur0.25"
                f"\\t(0,130,\\fscx104\\fscy104)\\t(130,220,\\fscx100\\fscy100)\\fad(70,120)}}{beat}"
            )

        cues = caption_cues(speech.words, script.narration, duration)
        for start, end, text in cues:
            if start >= cta_start:
                continue
            end = min(end, cta_start - 0.05)
            if end <= start + 0.12:
                continue
            caption = ass_wrap(text, 27, max_lines=2)
            events.append(
                f"Dialogue: 3,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
                f"{{\\fscx96\\fscy96\\t(0,90,\\fscx100\\fscy100)\\fad(35,45)}}{caption}"
            )

        if self.cfg.language == "ru":
            cta = f"{{\\1c{accent}}}BINANCE{{\\1c{white}}}\\NССЫЛКА В ПРОФИЛЕ\\N{{\\fs{fs_cta_small}\\1c&H00D6D6D6&}}реферальная ссылка"
        else:
            cta = f"{{\\1c{accent}}}BINANCE{{\\1c{white}}}\\NFIRST LINK IN PROFILE\\N{{\\fs{fs_cta_small}\\1c&H00D6D6D6&}}referral link"
        events.append(
            f"Dialogue: 6,{ass_time(cta_start)},{ass_time(max(cta_start+0.3, duration-0.06))},CTA,,0,0,0,,"
            f"{{\\pos({self.cfg.width//2},{round(self.cfg.height*0.47)})\\fscx91\\fscy91"
            f"\\t(0,180,\\fscx100\\fscy100)\\fad(130,100)}}{cta}"
        )
        path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")

    def _mux(self, base: Path, voice: Path, ass: Path, duration: float, work_dir: Path, out_path: Path) -> None:
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
    if not words:
        return []
    original = re.findall(r"\S+", narration)
    decorated: list[WordTiming] = []
    j = 0
    for timed in words:
        target = _norm_token(timed.text)
        chosen = timed.text
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
            unit_words = {
                "доллар", "доллара", "долларов", "рубль", "рубля", "рублей", "usd", "usdt", "eur", "btc", "eth",
                "час", "часа", "часов", "день", "дня", "дней", "сутки", "суток", "процент", "процента", "процентов",
                "тысяча", "тысячи", "тысяч", "миллион", "миллиона", "миллионов", "миллиард", "миллиарда", "миллиардов",
                "thousand", "million", "billion", "percent", "hours", "hour", "days", "day",
            }
            clean_w = re.sub(r"[^a-zа-яё]", "", w.text.casefold())
            glue_number = bool(group and numeric_piece(group[-1].text) and (numeric_piece(w.text) or clean_w in unit_words))
            if group and not glue_number and (len(group) >= 5 or next_chars > 31 or elapsed > 1.65):
                break
            group.append(w)
            chars = next_chars
            i += 1
            terminal = bool(re.search(r"[.!?…][\"')»]*$", w.text))
            soft = bool(re.search(r"[,;:][\"')»]*$", w.text))
            if i < len(words):
                next_clean = re.sub(r"[^a-zа-яё]", "", words[i].text.casefold())
                next_is_number_continuation = bool(numeric_piece(w.text) and (numeric_piece(words[i].text) or next_clean in unit_words))
            else:
                next_is_number_continuation = False
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
