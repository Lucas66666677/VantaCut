import math
import re
from collections.abc import Iterable

from app.ai.providers.schemas import Transcript, TranscriptSegment, WordTimestamp
from app.schemas.subtitle import SubtitleCue


def _cue_kinetic_style(words: list[WordTimestamp]) -> tuple[str, str]:
    ranked = max(words, key=lambda word: word.emotion_intensity, default=None)
    if ranked is None:
        return "none", "neutral"
    return ranked.animation_preset, ranked.emotion


def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def _format_ass_time(seconds: float) -> str:
    total_centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def transcript_to_cues(transcript: Transcript, output_offset: float, id_start: int) -> list[SubtitleCue]:
    """Convert ASR sentence segments (or word groups) to output-timeline-relative cues."""
    cues: list[SubtitleCue] = []
    source_segments = [segment for segment in transcript.segments if segment.end > segment.start and segment.text.strip()]
    if source_segments:
        for index, segment in enumerate(source_segments, start=id_start):
            shifted_words = [
                WordTimestamp(
                    word=word.word,
                    start=output_offset + word.start,
                    end=output_offset + word.end,
                    confidence=word.confidence,
                    emotion=word.emotion,
                    emotion_intensity=word.emotion_intensity,
                    animation_preset=word.animation_preset,
                    highlight_kind=word.highlight_kind,
                )
                for word in segment.words
            ]
            preset, emotion = _cue_kinetic_style(shifted_words)
            cues.append(SubtitleCue(
                id=f"subtitle-{index:04d}",
                start_time=output_offset + segment.start,
                end_time=output_offset + segment.end,
                text=segment.text.strip(),
                words=shifted_words,
                animation_preset=preset,  # type: ignore[arg-type]
                emotion=emotion,  # type: ignore[arg-type]
            ))
        return cues
    return _word_groups_to_cues(
        (word for segment in transcript.segments for word in segment.words), output_offset, id_start
    )


def _word_groups_to_cues(words: Iterable[WordTimestamp], output_offset: float, id_start: int) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    group: list[WordTimestamp] = []
    for word in words:
        group.append(word)
        ends_sentence = bool(re.search(r"[.!?。！？]$", word.word))
        if len(group) >= 8 or ends_sentence:
            cues.append(_cue_from_words(group, output_offset, id_start + len(cues)))
            group = []
    if group:
        cues.append(_cue_from_words(group, output_offset, id_start + len(cues)))
    return cues


def _cue_from_words(words: list[WordTimestamp], output_offset: float, index: int) -> SubtitleCue:
    text = " ".join(word.word for word in words).replace(" ，", "，").replace(" 。", "。")
    shifted_words = [
        WordTimestamp(
            word=word.word,
            start=output_offset + word.start,
            end=output_offset + word.end,
            confidence=word.confidence,
            emotion=word.emotion,
            emotion_intensity=word.emotion_intensity,
            animation_preset=word.animation_preset,
            highlight_kind=word.highlight_kind,
        )
        for word in words
    ]
    preset, emotion = _cue_kinetic_style(shifted_words)
    return SubtitleCue(
        id=f"subtitle-{index:04d}",
        start_time=shifted_words[0].start,
        end_time=shifted_words[-1].end,
        text=text,
        words=shifted_words,
        animation_preset=preset,  # type: ignore[arg-type]
        emotion=emotion,  # type: ignore[arg-type]
    )


def cues_to_srt(cues: list[SubtitleCue]) -> str:
    return "\n\n".join(
        f"{index}\n{_format_srt_time(cue.start_time)} --> {_format_srt_time(cue.end_time)}\n{cue.text}"
        for index, cue in enumerate(cues, start=1)
    ) + ("\n" if cues else "")


def _format_vtt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02}.{milliseconds:03}"


def cues_to_vtt(cues: list[SubtitleCue]) -> str:
    body = "\n\n".join(
        f"{cue.id}\n{_format_vtt_time(cue.start_time)} --> {_format_vtt_time(cue.end_time)}\n{cue.text}"
        for cue in cues
    )
    return "WEBVTT\n\n" + body + ("\n" if body else "")


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Kinetic,Noto Sans CJK TC,64,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,80,80,96,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _text_width(text: str, font_size: float = 64) -> float:
    # ASS/libass decides the exact glyph advance. This approximation only supplies a stable per-token anchor.
    return sum(font_size if "\u4e00" <= char <= "\u9fff" else font_size * 0.62 for char in text) + font_size * 0.16


def _token_layout(cue: SubtitleCue, *, width: int, height: int, vertical_position: float = .75) -> list[tuple[WordTimestamp, float, float]]:
    words = cue.words or [WordTimestamp(word=cue.text, start=cue.start_time, end=cue.end_time)]
    widths = [_text_width(word.word) for word in words]
    cursor = (width - sum(widths)) / 2
    layout: list[tuple[WordTimestamp, float, float]] = []
    for word, width in zip(words, widths):
        layout.append((word, cursor + width / 2, height * vertical_position))
        cursor += width
    return layout


def _dialogue(start: float, end: float, text: str, *, layer: int = 3) -> str:
    return f"Dialogue: {layer},{_format_ass_time(start)},{_format_ass_time(max(end, start + .05))},Kinetic,,0,0,0,,{text}"


def _word_tags(preset: str, x: float, y: float, duration_ms: int, highlight_kind: str = "none") -> str:
    settle = min(260, max(90, duration_ms))
    # ASS colours are BGR. These deliberately match the browser's yellow verbs and green numbers.
    highlight = "\\1c&H005BE6FF&" if highlight_kind == "verb" else "\\1c&H003DFFB8&" if highlight_kind == "number" else ""
    if preset == "spring":
        return f"{{\\an5\\pos({x:.0f},{y:.0f}){highlight}\\fscx70\\fscy70\\t(0,{settle // 2},\\fscx120\\fscy120)\\t({settle // 2},{settle},\\fscx100\\fscy100)}}"
    if preset == "pop":
        return f"{{\\an5\\move({x:.0f},{y + 78:.0f},{x:.0f},{y:.0f},0,{settle}){highlight}\\fscx132\\fscy132\\t(0,{settle},\\fscx100\\fscy100)\\fad(0,90)}}"
    if preset == "shake":
        shake_fill = highlight or "\\1c&H3A3AFF&"
        return f"{{\\an5\\pos({x:.0f},{y:.0f})\\bord4{shake_fill}\\t(0,{settle // 2},\\frz-4)\\t({settle // 2},{settle},\\frz4)}}"
    if preset == "float":
        return f"{{\\an5\\move({x:.0f},{y + 22:.0f},{x:.0f},{y:.0f},0,{settle}){highlight}\\alpha&H20&\\fad(120,160)}}"
    return f"{{\\an5\\pos({x:.0f},{y:.0f}){highlight}\\fscx96\\fscy96\\t(0,{settle},\\fscx100\\fscy100)}}"


def _explode_characters(word: WordTimestamp, x: float, y: float) -> list[str]:
    characters = list(word.word) or [word.word]
    spacing = 48.0
    start_x = x - spacing * (len(characters) - 1) / 2
    duration_ms = max(120, round((word.end - word.start) * 1000))
    lines: list[str] = []
    for index, character in enumerate(characters):
        # Deterministic radial vectors make the exported result reproducible across renders.
        angle = (sum(ord(char) for char in character) * 17 + index * 73) * math.pi / 180
        distance = 80 + (index % 4) * 32
        origin_x = start_x + index * spacing
        end_x, end_y = origin_x + math.cos(angle) * distance, y + math.sin(angle) * distance
        tags = (
            f"{{\\an5\\move({origin_x:.0f},{y:.0f},{end_x:.0f},{end_y:.0f},0,{duration_ms})"
            f"\\fscx125\\fscy125\\t(0,{duration_ms},\\fscx35\\fscy35\\alpha&HFF&)}}"
        )
        lines.append(_dialogue(word.start, word.end, tags + _escape_ass_text(character), layer=5))
    return lines


def _ass_header(*, preset: str, width: int, height: int) -> str:
    if preset == "viral_yellow":
        primary, secondary, outline, border, font_size = "&H0000D7FF", "&H00FFFFFF", "&H00101010", 5, 68
    elif preset == "karaoke_pop":
        primary, secondary, outline, border, font_size = "&H00FFFFFF", "&H0000D7FF", "&H00101010", 4, 68
    else:
        primary, secondary, outline, border, font_size = "&H00FFFFFF", "&H000000FF", "&H00101010", 3, 64
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Kinetic,Noto Sans CJK TC,{font_size},{primary},{secondary},{outline},&H80000000,1,0,0,0,100,100,0,0,1,{border},1,2,80,80,96,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def _karaoke_line(cue: SubtitleCue, *, x: float, y: float) -> str:
    words = cue.words or [WordTimestamp(word=cue.text, start=cue.start_time, end=cue.end_time)]
    chunks = []
    for word in words:
        centiseconds = max(1, round((word.end - word.start) * 100))
        chunks.append(f"{{\\k{centiseconds}}}" + _escape_ass_text(word.word))
    return _dialogue(cue.start_time, cue.end_time, f"{{\\an5\\pos({x:.0f},{y:.0f})\\fscx100\\fscy100}}" + " ".join(chunks), layer=2)


def cues_to_ass(
    cues: list[SubtitleCue], *, preset: str = "viral_yellow", aspect_ratio: str = "9:16", primary_y_ratio: float = .75,
) -> str:
    """Export word-level semantic motion as portable libass animation tags.

    `explode` is intentionally represented as independently animated characters; ASS cannot
    create true particles, while the WebM renderer can render physical particle sprites.
    """
    if preset not in {"viral_yellow", "karaoke_pop", "clean_white"}:
        raise ValueError("Unsupported caption preset")
    width, height = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)
    lines = []
    for cue in cues:
        if preset == "karaoke_pop":
            lines.append(_karaoke_line(cue, x=width / 2, y=height * primary_y_ratio))
        for word, x, y in _token_layout(cue, width=width, height=height, vertical_position=primary_y_ratio):
            if word.animation_preset == "explode":
                lines.extend(_explode_characters(word, x, y))
                continue
            duration_ms = max(80, round((word.end - word.start) * 1000))
            motion = word.animation_preset
            if preset == "viral_yellow" and motion == "none":
                motion = "spring"
            elif preset == "clean_white":
                motion = "none"
            lines.append(_dialogue(
                word.start, word.end,
                _word_tags(motion, x, y, duration_ms, word.highlight_kind) + _escape_ass_text(word.word),
            ))
    return _ass_header(preset=preset, width=width, height=height) + "\n".join(lines) + ("\n" if lines else "")
