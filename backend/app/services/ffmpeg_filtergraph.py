from dataclasses import dataclass
import asyncio
import inspect
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.audio_enhancement import NOISE_REDUCTION_FILTER, has_noise_reduction
from app.services.auto_reframe import AutoReframePlan
from app.schemas.keyframes import ClipTransformAnimation
from app.schemas.speed_curves import ClipSpeedCurve
from app.services.keyframe_animation import FFmpegKeyframeCompiler
from app.schemas.transitions import TransitionSpec
from app.services.transitions import ffmpeg_transition_filter
from app.core.config import settings
from app.services.video_encoder import resolve_video_encoder
from app.services.beauty_enhancement import BeautyEnhancement


class FiltergraphBuildError(ValueError):
    pass


@dataclass(frozen=True)
class TimelineSegment:
    source_start: float
    source_end: float
    input_index: int = 0
    action: str = "keep"
    audio_effects: tuple[str, ...] = ()
    gain_db: float = 0.0
    clip_id: str | None = None

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


@dataclass(frozen=True)
class BRollSegment:
    source_start: float
    source_end: float
    timeline_start: float
    input_index: int
    z_index: int = 10
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    kind: str | None = None

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


@dataclass(frozen=True)
class ExportProfile:
    resolution: str = "1080p"
    aspect_ratio: str = "16:9"

    def dimensions(self) -> tuple[int, int]:
        profiles = {
            ("1080p", "16:9"): (1920, 1080),
            ("1080p", "9:16"): (1080, 1920),
            ("1080p", "1:1"): (1080, 1080),
            ("720p", "16:9"): (1280, 720),
            ("720p", "9:16"): (720, 1280),
            ("720p", "1:1"): (720, 720),
            ("4k", "16:9"): (3840, 2160),
            ("4k", "9:16"): (2160, 3840),
        }
        try:
            return profiles[(self.resolution, self.aspect_ratio)]
        except KeyError as exc:
            raise FiltergraphBuildError(
                "Supported export profiles are 1080p/720p with 16:9, 9:16, or 1:1 aspect ratios"
            ) from exc

    @property
    def video_bitrate(self) -> str:
        return {"4k": "20M", "1080p": "8M", "720p": "4M"}[self.resolution]

    @property
    def video_buffer_size(self) -> str:
        return {"4k": "40M", "1080p": "16M", "720p": "8M"}[self.resolution]


@dataclass(frozen=True)
class HDRDeliveryProfile:
    """Rec.2020 HDR delivery settings; HDR10 is specifically 10-bit PQ."""

    transfer: str = "pq"  # pq (ST 2084) or hlg (ARIB STD-B67)
    bit_depth: int = 10
    hdr10_metadata: bool = True
    master_display: str = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)"
    max_cll: int = 1000
    max_fall: int = 400

    def __post_init__(self) -> None:
        if self.transfer not in {"pq", "hlg"}:
            raise FiltergraphBuildError("HDR transfer must be pq or hlg")
        if self.bit_depth not in {10, 12}:
            raise FiltergraphBuildError("HDR delivery supports 10-bit or 12-bit output")
        if self.hdr10_metadata and (self.transfer != "pq" or self.bit_depth != 10):
            raise FiltergraphBuildError("HDR10 metadata requires 10-bit PQ output")

    @property
    def ffmpeg_transfer(self) -> str:
        return "smpte2084" if self.transfer == "pq" else "arib-std-b67"

    @property
    def pixel_format(self) -> str:
        return f"yuv420p{self.bit_depth}le"

    def metadata_args(self, codec: str) -> list[str]:
        args = [
            "-color_primaries", "bt2020",
            "-color_trc", self.ffmpeg_transfer,
            "-colorspace", "bt2020nc",
            "-color_range", "tv",
        ]
        if self.hdr10_metadata:
            args.extend(["-master_display", self.master_display, "-max_cll", f"{self.max_cll},{self.max_fall}"])
            if codec == "libx265":
                args.extend([
                    "-x265-params",
                    f"hdr10=1:hdr10-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
                    f"colormatrix=bt2020nc:master-display={self.master_display}:max-cll={self.max_cll},{self.max_fall}",
                ])
        return args


class RenderProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderProcessResult:
    return_code: int
    stdout: str
    stderr: str


ProgressCallback = Callable[[int], Awaitable[None] | None]


class FFmpegFiltergraphBuilder:
    """Build an A/V-safe FFmpeg filtergraph for one source video and a confirmed timeline."""

    def __init__(
        self,
        timeline_json: dict[str, Any] | list[dict[str, Any]],
        input_index: int = 0,
        *,
        auto_reframe_plan: AutoReframePlan | None = None,
        reframe_command_path: str | None = None,
        screen_focus_command_path: str | None = None,
    ) -> None:
        self.input_index = input_index
        if (auto_reframe_plan is None) != (reframe_command_path is None):
            raise FiltergraphBuildError("Auto-reframe requires both plan and sendcmd file")
        self.auto_reframe_plan = auto_reframe_plan
        self.reframe_command_path = reframe_command_path
        raw_segments = self._main_segments_from_timeline(timeline_json)
        self.segments = self._parse_segments(raw_segments)
        self.b_roll_segments = self._parse_b_roll_segments(timeline_json)
        self.auto_pip = self._parse_auto_pip(timeline_json)
        self.screen_focus_effects = self._parse_screen_focus_effects(timeline_json)
        self.keyframe_animations = self._parse_keyframe_animations(timeline_json)
        self.speed_curves = self._parse_speed_curves(timeline_json)
        self.transitions = self._parse_transitions(timeline_json)
        self.beat_effects = self._parse_beat_effects(timeline_json)
        self.hook_rescue = self._parse_hook_rescue(timeline_json)
        self.visual_hooks = self._parse_visual_hooks(timeline_json)
        self.fitness_overlay = self._parse_fitness_overlay(timeline_json)
        self.profanity_overlays = self._parse_profanity_overlays(timeline_json)
        self.vertical_dual_layout = self._parse_vertical_dual_layout(timeline_json)
        self.transition_input_indexes = self._parse_transition_input_indexes(timeline_json)
        if self.screen_focus_effects and not screen_focus_command_path:
            raise FiltergraphBuildError("Screen-focus effects require an FFmpeg sendcmd file")
        self.screen_focus_command_path = screen_focus_command_path

    @staticmethod
    def _main_segments_from_timeline(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(timeline_json, dict):
            return timeline_json
        if "tracks" not in timeline_json:
            return timeline_json.get("segments", [])
        for track in timeline_json["tracks"]:
            if track.get("type") == "main_video":
                return track.get("clips", [])
        return []

    @staticmethod
    def _format_time(value: float) -> str:
        # Fixed precision avoids scientific notation and keeps generated commands readable.
        return f"{value:.6f}".rstrip("0").rstrip(".") or "0"

    def _parse_segments(self, raw_segments: list[dict[str, Any]]) -> list[TimelineSegment]:
        segments: list[TimelineSegment] = []
        for raw in raw_segments:
            if raw.get("action", "keep") != "keep":
                continue
            try:
                segment = TimelineSegment(
                    source_start=float(raw["source_start"]),
                    source_end=float(raw["source_end"]),
                    input_index=int(raw.get("input_index", self.input_index)),
                    action="keep",
                    audio_effects=tuple(raw.get("audio_effects", [])),
                    gain_db=float(raw.get("gain_db", 0)),
                    clip_id=str(raw["id"]) if raw.get("id") is not None else None,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Each keep segment requires numeric source_start and source_end") from exc
            if segment.source_start < 0 or segment.source_end <= segment.source_start:
                raise FiltergraphBuildError("Each keep segment must satisfy 0 <= source_start < source_end")
            segments.append(segment)

        if not segments:
            raise FiltergraphBuildError("Timeline contains no keep segments to render")
        return segments

    @staticmethod
    def _parse_keyframe_animations(
        timeline_json: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, ClipTransformAnimation]:
        """Read persisted per-Clip animation settings and reject malformed render instructions early."""
        if not isinstance(timeline_json, dict):
            return {}
        document = timeline_json.get("motion_keyframes", {})
        raw_animations = document.get("animations", []) if isinstance(document, dict) else []
        parsed: dict[str, ClipTransformAnimation] = {}
        for raw in raw_animations:
            try:
                animation = ClipTransformAnimation.model_validate(raw)
            except (TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Invalid motion_keyframes animation") from exc
            if animation.clip_id is None:
                raise FiltergraphBuildError("Motion keyframes must target a concrete clip_id")
            parsed[str(animation.clip_id)] = animation
        return parsed

    @staticmethod
    def _parse_speed_curves(
        timeline_json: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, ClipSpeedCurve]:
        if not isinstance(timeline_json, dict):
            return {}
        document = timeline_json.get("speed_curves", {})
        raw_curves = document.get("curves", []) if isinstance(document, dict) else []
        parsed: dict[str, ClipSpeedCurve] = {}
        for raw in raw_curves:
            try:
                curve = ClipSpeedCurve.model_validate(raw)
            except (TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Invalid speed curve") from exc
            parsed[str(curve.clip_id)] = curve
        return parsed

    def _speed_sections(self, segment: TimelineSegment) -> list[tuple[float, float, float]]:
        """Convert normalised UI nodes into source-time sections with a stable mean speed."""
        curve = self.speed_curves.get(segment.clip_id or "")
        if curve is None:
            return [(segment.source_start, segment.source_end, 1.0)]
        sections: list[tuple[float, float, float]] = []
        # Four short linearly-sampled sections per UI edge retain sharp slow-motion
        # valleys without exploding an ordinary five-node curve into hundreds of filters.
        for left, right in zip(curve.points, curve.points[1:]):
            for subdivision in range(4):
                left_ratio = left.position + (right.position - left.position) * subdivision / 4
                right_ratio = left.position + (right.position - left.position) * (subdivision + 1) / 4
                midpoint = (subdivision + .5) / 4
                speed = left.speed + (right.speed - left.speed) * midpoint
                start = segment.source_start + segment.duration * left_ratio
                end = segment.source_start + segment.duration * right_ratio
                sections.append((start, end, speed))
        return sections

    def _rendered_duration(self, segment: TimelineSegment) -> float:
        return sum((end - start) / speed for start, end, speed in self._speed_sections(segment))

    @staticmethod
    def _atempo_chain(speed: float) -> str:
        """FFmpeg atempo accepts 0.5–2.0; chain filters for 0.1–10x UI speeds."""
        remaining = speed; factors: list[float] = []
        while remaining > 2:
            factors.append(2.0); remaining /= 2
        while remaining < .5:
            factors.append(.5); remaining /= .5
        factors.append(remaining)
        return ",".join(f"atempo={value:.6f}" for value in factors)

    @staticmethod
    def _parse_transitions(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[tuple[str, str], TransitionSpec]:
        if not isinstance(timeline_json, dict): return {}
        document = timeline_json.get("transition_graph", {})
        raw_specs = document.get("transitions", []) if isinstance(document, dict) else timeline_json.get("transitions", [])
        result: dict[tuple[str, str], TransitionSpec] = {}
        for raw in raw_specs:
            try: spec = TransitionSpec.model_validate(raw)
            except (TypeError, ValueError) as exc: raise FiltergraphBuildError("Invalid transition specification") from exc
            result[(str(spec.from_clip_id), str(spec.to_clip_id))] = spec
        return result

    @staticmethod
    def _parse_transition_input_indexes(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, int]:
        if not isinstance(timeline_json, dict): return {}
        document = timeline_json.get("transition_graph", {})
        raw_specs = document.get("transitions", []) if isinstance(document, dict) else []
        return {str(item["id"]): int(item["render_input_index"]) for item in raw_specs if item.get("render_input_index") is not None}

    @staticmethod
    def _parse_vertical_dual_layout(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(timeline_json, dict): return None
        raw = timeline_json.get("vertical_dual_layout", {})
        document = raw.get("plan", raw) if isinstance(raw, dict) else {}
        if not isinstance(document, dict) or not document: return None
        try:
            top_ratio = float(document["top_ratio"]); face = dict(document["face_crop"]); gameplay = dict(document["gameplay_crop"])
            parsed = {"top_ratio": top_ratio, "face_crop": {key: int(face[key]) for key in ("x", "y", "width", "height")}, "gameplay_crop": {key: int(gameplay[key]) for key in ("x", "y", "width", "height")}}
        except (KeyError, TypeError, ValueError) as exc:
            raise FiltergraphBuildError("Invalid vertical dual-screen layout plan") from exc
        if not .30 <= top_ratio <= .60 or any(value < 0 for crop in (parsed["face_crop"], parsed["gameplay_crop"]) for value in crop.values()) or any(crop["width"] < 2 or crop["height"] < 2 for crop in (parsed["face_crop"], parsed["gameplay_crop"])):
            raise FiltergraphBuildError("Vertical dual-screen crop plan is out of range")
        return parsed

    @staticmethod
    def _parse_beat_effects(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(timeline_json, dict): return []
        raw = dict(timeline_json.get("beat_sync_montage", {})).get("effects", [])
        result: list[dict[str, Any]] = []
        for effect in raw if isinstance(raw, list) else []:
            try:
                time, duration, kind = float(effect["time"]), float(effect.get("duration", .1)), str(effect["kind"])
            except (KeyError, TypeError, ValueError) as exc: raise FiltergraphBuildError("Invalid beat-sync effect") from exc
            if time < 0 or not .02 <= duration <= .5 or kind not in {"white_flash", "black_flash", "camera_shake"}: raise FiltergraphBuildError("Invalid beat-sync effect range")
            result.append({"time": time, "duration": duration, "kind": kind})
        return result

    @staticmethod
    def _parse_hook_rescue(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, float] | None:
        if not isinstance(timeline_json, dict):
            return None
        raw = dict(timeline_json.get("hook_rescue") or {})
        if raw.get("status") != "applied":
            return None
        try:
            grayscale = float(raw.get("grayscale_seconds", .35)); fade = float(raw.get("color_fade_seconds", .55))
        except (TypeError, ValueError) as exc:
            raise FiltergraphBuildError("Invalid Hook rescue color transition") from exc
        if not 0 <= grayscale <= 2 or not .05 <= fade <= 2:
            raise FiltergraphBuildError("Invalid Hook rescue color-transition range")
        return {"grayscale_seconds": grayscale, "color_fade_seconds": fade}

    @staticmethod
    def _parse_visual_hooks(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(timeline_json, dict):
            return None
        raw = dict(timeline_json.get("visual_hooks") or {})
        if raw.get("status") != "configured":
            return None
        try:
            duration = float(raw["timeline_duration"]); style = str(raw["style"]); platform = str(raw["platform"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FiltergraphBuildError("Invalid visual-hook configuration") from exc
        if duration <= 0 or style not in {"gradient_line", "liquid_fill", "border_marquee"} or platform not in {"tiktok", "instagram_reels", "youtube_shorts"}:
            raise FiltergraphBuildError("Visual-hook configuration is out of range")
        suspense = dict(raw.get("suspense") or {})
        if suspense.get("enabled"):
            try:
                start, end, text = float(suspense["start"]), float(suspense["end"]), str(suspense["text"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Invalid visual-hook suspense configuration") from exc
            if not 0 <= start < end <= duration or len(text) > 80 or any(character in text for character in "':\\"):
                raise FiltergraphBuildError("Visual-hook suspense text is invalid")
            suspense = {"enabled": True, "start": start, "end": end, "text": text}
        else:
            suspense = {"enabled": False}
        return {"duration": duration, "style": style, "platform": platform, "suspense": suspense}

    @staticmethod
    def _parse_fitness_overlay(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(timeline_json, dict): return None
        raw = dict(timeline_json.get("fitness_overlay") or {})
        if raw.get("status") != "completed": return None
        style = str(raw.get("hud_style", "impact")); target_reps = int(raw.get("target_reps", 10))
        if style not in {"impact", "neon", "minimal"} or not 1 <= target_reps <= 100:
            raise FiltergraphBuildError("Invalid fitness overlay configuration")
        events: list[dict[str, float | int]] = []
        for raw_event in raw.get("events", []):
            try: rep, timestamp = int(raw_event["rep"]), float(raw_event["timeline_time"])
            except (KeyError, TypeError, ValueError) as exc: raise FiltergraphBuildError("Invalid fitness repetition event") from exc
            if rep < 1 or timestamp < 0: raise FiltergraphBuildError("Fitness repetition event is out of range")
            events.append({"rep": rep, "timeline_time": timestamp})
        if not events: return None
        fatigue = raw.get("fatigue_event"); fatigue_time: float | None = None
        if isinstance(fatigue, dict):
            try: fatigue_time = float(fatigue["timeline_time"])
            except (KeyError, TypeError, ValueError) as exc: raise FiltergraphBuildError("Invalid fitness fatigue event") from exc
            if fatigue_time < 0: raise FiltergraphBuildError("Fitness fatigue event is out of range")
        return {"style": style, "target_reps": target_reps, "events": events, "fatigue_time": fatigue_time}

    @staticmethod
    def _parse_profanity_overlays(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(timeline_json, dict): return []
        document = dict(timeline_json.get("profanity_filter") or {})
        emoji_path = document.get("emoji_path")
        if document.get("status") != "completed" or not emoji_path: return []
        parsed: list[dict[str, Any]] = []
        for event in document.get("events", []):
            try:
                position = dict(event["mouth_position"]); start, end = float(event["start_time"]), float(event["end_time"])
                x, y, scale = float(position["x"]), float(position["y"]), float(position.get("scale", .14))
            except (KeyError, TypeError, ValueError) as exc: raise FiltergraphBuildError("Invalid profanity mouth-overlay event") from exc
            if start < 0 or end <= start or not 0 <= x <= 1 or not 0 <= y <= 1: raise FiltergraphBuildError("Invalid profanity mouth-overlay range")
            parsed.append({"start": start, "end": end, "x": x, "y": y, "size": int(max(92, min(280, scale * 1080))), "emoji_path": str(emoji_path)})
        return parsed

    def _parse_b_roll_segments(self, timeline_json: dict[str, Any] | list[dict[str, Any]]) -> list[BRollSegment]:
        if not isinstance(timeline_json, dict):
            return []
        segments: list[BRollSegment] = []
        for track in timeline_json.get("tracks", []):
            if track.get("type") != "b_roll":
                continue
            track_z_index = int(track.get("z_index", 10))
            for raw in track.get("clips", []):
                if raw.get("action", "keep") != "keep":
                    continue
                try:
                    segment = BRollSegment(
                        source_start=float(raw["source_start"]),
                        source_end=float(raw["source_end"]),
                        timeline_start=float(raw["timeline_start"]),
                        input_index=int(raw.get("input_index", 1)),
                        z_index=int(raw.get("z_index", track_z_index)),
                        fade_in_seconds=float(raw.get("fade_in_seconds", 0)),
                        fade_out_seconds=float(raw.get("fade_out_seconds", 0)),
                        kind=str(raw.get("kind")) if raw.get("kind") else None,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise FiltergraphBuildError(
                        "Each B-Roll clip requires source_start, source_end, timeline_start, and input_index"
                    ) from exc
                if segment.source_start < 0 or segment.source_end <= segment.source_start or segment.timeline_start < 0:
                    raise FiltergraphBuildError("B-Roll clip timestamps are invalid")
                if min(segment.fade_in_seconds, segment.fade_out_seconds) < 0 or segment.fade_in_seconds + segment.fade_out_seconds >= segment.duration:
                    raise FiltergraphBuildError("B-Roll fade durations are invalid")
                segments.append(segment)
        return sorted(segments, key=lambda item: (item.z_index, item.timeline_start))

    @staticmethod
    def _parse_auto_pip(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(timeline_json, dict):
            return None
        raw = dict(timeline_json.get("auto_pip") or {})
        if raw.get("status") != "completed":
            return None
        layout, corner = dict(raw.get("pip_layout") or {}), str(raw.get("corner", "bottom_right"))
        try:
            scale, padding = float(layout.get("scale", .28)), float(layout.get("padding", .04))
        except (TypeError, ValueError) as exc:
            raise FiltergraphBuildError("Invalid Auto-PiP layout") from exc
        if corner not in {"top_left", "top_right", "bottom_left", "bottom_right"} or not .12 <= scale <= .55 or not 0 <= padding <= .12:
            raise FiltergraphBuildError("Auto-PiP layout is out of range")
        events: list[dict[str, float]] = []
        for event in raw.get("focus_events", []):
            try:
                start, end = float(event["start_time"]), float(event["end_time"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Invalid Auto-PiP focus event") from exc
            if start < 0 or end <= start:
                raise FiltergraphBuildError("Auto-PiP focus event is out of range")
            events.append({"start": start, "end": end})
        return {"scale": scale, "padding": padding, "corner": corner, "focus_events": events}

    @staticmethod
    def _parse_screen_focus_effects(timeline_json: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(timeline_json, dict):
            return []
        effects: list[dict[str, Any]] = []
        for raw in timeline_json.get("screen_focus_effects", []):
            try:
                start, end, zoom = float(raw["output_start"]), float(raw["output_end"]), float(raw.get("zoom", 1.7))
                bbox = dict(raw["target_bbox_norm"])
                if start < 0 or end <= start or zoom <= 1 or any(float(bbox[key]) < 0 for key in ("x", "y", "width", "height")):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise FiltergraphBuildError("Invalid screen_focus_effects entry") from exc
            effects.append({**raw, "output_start": start, "output_end": end, "zoom": zoom, "target_bbox_norm": bbox})
        return sorted(effects, key=lambda item: item["output_start"])

    @staticmethod
    def _escape_subtitle_filename(path: str) -> str:
        """Escape a local filename for FFmpeg's subtitles filter expression."""
        normalised = Path(path).as_posix()
        return (
            normalised
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace(",", "\\,")
        )

    @staticmethod
    def _escape_filter_filename(path: str) -> str:
        return FFmpegFiltergraphBuilder._escape_subtitle_filename(path)

    def build_complex_filter(
        self,
        video_label: str = "outv",
        audio_label: str = "outa",
        *,
        subtitle_path: str | None = None,
        lut_path: str | None = None,
        lut_intensity: float = 1.0,
        ocio_display_lut_path: str | None = None,
        watermark_input_index: int | None = None,
        hdr_profile: HDRDeliveryProfile | None = None,
        export_profile: ExportProfile | None = None,
        motion_fps: float = 30.0,
        beauty_enhancement: BeautyEnhancement | None = None,
    ) -> str:
        filters: list[str] = []
        profile_for_animation = export_profile or ExportProfile()
        concat_inputs: list[str] = []
        source_video_label = f"{self.input_index}:v"
        if self.auto_reframe_plan is not None and self.reframe_command_path is not None:
            commands_path = self._escape_filter_filename(self.reframe_command_path)
            plan = self.auto_reframe_plan
            filters.append(
                f"[{source_video_label}]sendcmd=f='{commands_path}',"
                f"crop@auto_reframe=w={plan.crop_width}:h={plan.crop_height}:"
                f"x=(iw-ow)/2:y=(ih-oh)/2[reframedv]"
            )
            source_video_label = "reframedv"

        for index, segment in enumerate(self.segments):
            video_segment_label = f"v{index}"
            audio_segment_label = f"a{index}"
            segment_video_label = source_video_label if segment.input_index == self.input_index else f"{segment.input_index}:v"
            sections = self._speed_sections(segment)
            has_speed_curve = segment.clip_id is not None and segment.clip_id in self.speed_curves
            if has_speed_curve:
                video_parts: list[str] = []; audio_parts: list[str] = []
                for section_index, (section_start, section_end, speed) in enumerate(sections):
                    start, end = self._format_time(section_start), self._format_time(section_end)
                    video_part, audio_part = f"speedv{index}_{section_index}", f"speeda{index}_{section_index}"
                    video_filter = f"[{segment_video_label}]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{speed:.6f}"
                    # The slow section is timestamp-expanded before optical-flow interpolation, so
                    # minterpolate synthesises temporal detail at the intended output cadence.
                    if speed < .5:
                        if motion_fps <= 0:
                            raise FiltergraphBuildError("motion_fps must be positive")
                        video_filter += f",minterpolate=fps={motion_fps:.3f}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir"
                    filters.append(f"{video_filter}[{video_part}]")
                    audio_filter = f"[{segment.input_index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,{self._atempo_chain(speed)}"
                    if has_noise_reduction(list(segment.audio_effects)):
                        audio_filter += f",{NOISE_REDUCTION_FILTER}"
                    if segment.gain_db:
                        audio_filter += f",volume={self._format_time(segment.gain_db)}dB"
                    filters.append(f"{audio_filter}[{audio_part}]")
                    video_parts.append(video_part); audio_parts.append(audio_part)
                concat_args = "".join(f"[{video_parts[item]}][{audio_parts[item]}]" for item in range(len(sections)))
                filters.append(f"{concat_args}concat=n={len(sections)}:v=1:a=1[{video_segment_label}][{audio_segment_label}]")
            else:
                start, end = self._format_time(segment.source_start), self._format_time(segment.source_end)
                filters.append(f"[{segment_video_label}]trim=start={start}:end={end},setpts=PTS-STARTPTS[{video_segment_label}]")
                audio_filter = f"[{segment.input_index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS"
                if has_noise_reduction(list(segment.audio_effects)):
                    audio_filter += f",{NOISE_REDUCTION_FILTER}"
                if segment.gain_db:
                    audio_filter += f",volume={self._format_time(segment.gain_db)}dB"
                filters.append(f"{audio_filter}[{audio_segment_label}]")
            animation = self.keyframe_animations.get(segment.clip_id or "")
            if animation is not None:
                width, height = profile_for_animation.dimensions()
                animated_label = f"motionv{index}"
                if motion_fps <= 0:
                    raise FiltergraphBuildError("motion_fps must be positive")
                filters.append(
                    f"[{video_segment_label}]{FFmpegKeyframeCompiler(animation, fps=motion_fps).zoompan_filter(width=width, height=height)}"
                    f"[{animated_label}]"
                )
                video_segment_label = animated_label
            concat_inputs.extend([f"[{video_segment_label}]", f"[{audio_segment_label}]"])

        effective_profile = export_profile or (ExportProfile() if self.b_roll_segments or self.vertical_dual_layout else None)
        if self.vertical_dual_layout is not None:
            effective_profile = ExportProfile(resolution=(effective_profile or ExportProfile()).resolution, aspect_ratio="9:16")
        requires_video_post_processing = (
            subtitle_path is not None or lut_path is not None or ocio_display_lut_path is not None
            or watermark_input_index is not None or hdr_profile is not None or effective_profile is not None
            or bool(self.screen_focus_effects) or bool(self.keyframe_animations) or bool(self.speed_curves)
            or bool(self.beat_effects) or self.hook_rescue is not None or self.visual_hooks is not None or self.fitness_overlay is not None or bool(self.profanity_overlays) or self.vertical_dual_layout is not None
        )
        concat_video_label = "concatv" if requires_video_post_processing else video_label
        if self.transitions and len(self.segments) > 1:
            current_video, current_audio, current_duration = "v0", "a0", self._rendered_duration(self.segments[0])
            for index, segment in enumerate(self.segments[1:], start=1):
                next_video, next_audio = f"v{index}", f"a{index}"
                spec = self.transitions.get((self.segments[index - 1].clip_id or "", segment.clip_id or ""))
                next_video_label, next_audio_label = f"joinedv{index}", f"joineda{index}"
                if spec is None:
                    filters.append(f"[{current_video}][{current_audio}][{next_video}][{next_audio}]concat=n=2:v=1:a=1[{next_video_label}][{next_audio_label}]")
                    current_duration += self._rendered_duration(segment)
                else:
                    duration = min(spec.duration_seconds, current_duration - .01, segment.duration - .01)
                    if duration <= .01: raise FiltergraphBuildError("Transition is longer than an adjacent clip")
                    plate_index = self.transition_input_indexes.get(spec.id)
                    if plate_index is not None:
                        # Pre-rendered depth/flow plate replaces d seconds on either side of the cut.
                        before_label, plate_label, after_label = f"beforev{index}", f"platev{index}", f"afterv{index}"
                        filters.append(f"[{current_video}]trim=start=0:end={current_duration - duration:.6f},setpts=PTS-STARTPTS[{before_label}]")
                        filters.append(f"[{plate_index}:v]trim=start=0:end={duration:.6f},setpts=PTS-STARTPTS[{plate_label}]")
                        filters.append(f"[{next_video}]trim=start={duration:.6f},setpts=PTS-STARTPTS[{after_label}]")
                        filters.append(f"[{before_label}][{plate_label}][{after_label}]concat=n=3:v=1:a=0[{next_video_label}]")
                    else:
                        transition_filter = ffmpeg_transition_filter(spec, offset_seconds=current_duration - duration, gltransition_available=settings.ffmpeg_gltransition_enabled)
                        filters.append(f"[{current_video}][{next_video}]{transition_filter}[{next_video_label}]")
                    filters.append(f"[{current_audio}][{next_audio}]acrossfade=d={duration:.6f}:c1=tri:c2=tri[{next_audio_label}]")
                    current_duration += self._rendered_duration(segment) - duration
                current_video, current_audio = next_video_label, next_audio_label
            filters.append(f"[{current_video}]null[{concat_video_label}]")
            filters.append(f"[{current_audio}]anull[{audio_label}]")
        else:
            filters.append(f"{''.join(concat_inputs)}concat=n={len(self.segments)}:v=1:a=1[{concat_video_label}][{audio_label}]")

        current_video_label = concat_video_label
        if self.screen_focus_effects and self.screen_focus_command_path is not None:
            commands_path = self._escape_filter_filename(self.screen_focus_command_path)
            filters.append(
                f"[{current_video_label}]sendcmd=f='{commands_path}',"
                "crop@screen_focus=w=iw:h=ih:x=0:y=0[screenfocusv]"
            )
            current_video_label = "screenfocusv"
            # The crop follows the referenced UI target, placing it near centre. Draw the highlight in
            # crop-relative coordinates so it moves naturally with the zoomed region.
            for index, effect in enumerate(self.screen_focus_effects):
                bbox = effect["target_bbox_norm"]
                box_width = min(0.72, max(0.14, float(bbox["width"]) * float(effect["zoom"]) * 1.55))
                box_height = min(0.72, max(0.10, float(bbox["height"]) * float(effect["zoom"]) * 1.75))
                label = f"screenhighlight{index}"
                start, end = self._format_time(effect["output_start"]), self._format_time(effect["output_end"])
                filters.append(
                    f"[{current_video_label}]drawbox=x=iw*{(1 - box_width) / 2:.5f}:"
                    f"y=ih*{(1 - box_height) / 2:.5f}:w=iw*{box_width:.5f}:h=ih*{box_height:.5f}:"
                    f"color=yellow@0.35:t=fill:enable='between(t\\,{start}\\,{end})'[{label}]"
                )
                current_video_label = label

        if effective_profile is not None:
            width, height = effective_profile.dimensions()
            if self.vertical_dual_layout is not None:
                plan = self.vertical_dual_layout; top_height = int(height * plan["top_ratio"]) // 2 * 2; bottom_height = height - top_height
                face, gameplay = plan["face_crop"], plan["gameplay_crop"]
                filters.append(f"[{current_video_label}]split=3[dualbgsrc][dualfacesrc][dualgamesrc]")
                filters.append(f"[dualbgsrc]scale=w={width}:h={height}:force_original_aspect_ratio=increase,crop=w={width}:h={height},gblur=sigma=24[dualbg]")
                filters.append(f"[dualfacesrc]crop=w={face['width']}:h={face['height']}:x={face['x']}:y={face['y']},scale=w={width}:h={top_height}:force_original_aspect_ratio=increase,crop=w={width}:h={top_height}[dualface]")
                filters.append(f"[dualgamesrc]crop=w={gameplay['width']}:h={gameplay['height']}:x={gameplay['x']}:y={gameplay['y']},scale=w={width}:h={bottom_height}:force_original_aspect_ratio=increase,crop=w={width}:h={bottom_height}[dualgame]")
                filters.append(f"[dualbg][dualface]overlay=x=0:y=0:eof_action=pass[dualupper]")
                filters.append(f"[dualupper][dualgame]overlay=x=0:y={top_height}:eof_action=pass,drawbox=x=0:y={top_height - 3}:w=iw:h=6:color=white@0.82:t=fill[duallayoutv]")
                current_video_label = "duallayoutv"
            else:
                filters.append(
                    f"[{current_video_label}]scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
                    f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black[scaledv]"
                )
                current_video_label = "scaledv"

            # During sustained selfie speech, soften the primary source before
            # the full-screen presenter overlay is enabled. The original audio
            # path is deliberately untouched.
            if self.auto_pip and self.auto_pip["focus_events"]:
                focus_expression = "+".join(f"between(t\\,{item['start']:.3f}\\,{item['end']:.3f})" for item in self.auto_pip["focus_events"])
                filters.append(f"[{current_video_label}]split=2[pipmain][pipblurinput]")
                filters.append("[pipblurinput]gblur=sigma=8[pipblur]")
                filters.append(f"[pipmain][pipblur]overlay=x=0:y=0:enable='{focus_expression}'[pipmainfocus]")
                current_video_label = "pipmainfocus"

            # B-Roll is video-only. Offsetting PTS places its first frame at timeline_start;
            # overlay never touches [outa], so the main speaker's audio remains uninterrupted.
            for index, b_roll in enumerate(self.b_roll_segments):
                broll_label = f"broll{index}"
                overlay_label = f"overlay{index}"
                if b_roll.kind == "auto_pip_selfie" and self.auto_pip is not None:
                    scale, padding, corner = self.auto_pip["scale"], self.auto_pip["padding"], self.auto_pip["corner"]
                    small_width = max(2, int(width * scale) // 2 * 2)
                    x = f"{int(width * padding)}" if corner.endswith("left") else f"W-w-{int(width * padding)}"
                    y = f"{int(height * padding)}" if corner.startswith("top") else f"H-h-{int(height * padding)}"
                    source = f"[{b_roll.input_index}:v]trim=start={self._format_time(b_roll.source_start)}:end={self._format_time(b_roll.source_end)},setpts=PTS-STARTPTS"
                    focus_events = self.auto_pip["focus_events"]
                    if focus_events:
                        filters.append(f"{source},split=2[pipsmallsrc{index}][pipfullsrc{index}]")
                        small_source, full_source = f"[pipsmallsrc{index}]", f"[pipfullsrc{index}]"
                    else:
                        filters.append(f"{source}[pipsmallsrc{index}]")
                        small_source, full_source = f"[pipsmallsrc{index}]", ""
                    filters.append(f"{small_source}scale=w={small_width}:h=-2:force_original_aspect_ratio=decrease,format=rgba,setpts=PTS-STARTPTS+{self._format_time(b_roll.timeline_start)}/TB[pipsmall{index}]")
                    filters.append(f"[{current_video_label}][pipsmall{index}]overlay=x={x}:y={y}:eof_action=pass:shortest=0[pipcorner{index}]")
                    if focus_events:
                        focus_expression = "+".join(f"between(t\\,{item['start']:.3f}\\,{item['end']:.3f})" for item in focus_events)
                        filters.append(f"{full_source}scale=w={width}:h={height}:force_original_aspect_ratio=decrease,pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black,format=rgba,setpts=PTS-STARTPTS+{self._format_time(b_roll.timeline_start)}/TB[pipfocus{index}]")
                        filters.append(f"[pipcorner{index}][pipfocus{index}]overlay=x=0:y=0:eof_action=pass:enable='{focus_expression}'[{overlay_label}]")
                    else:
                        filters.append(f"[pipcorner{index}]null[{overlay_label}]")
                    current_video_label = overlay_label
                    continue
                filters.append(
                    f"[{b_roll.input_index}:v]trim=start={self._format_time(b_roll.source_start)}:"
                    f"end={self._format_time(b_roll.source_end)},setpts=PTS-STARTPTS,"
                    f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
                    f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black,format=rgba"
                    f"{',fade=t=in:st=0:d=' + self._format_time(b_roll.fade_in_seconds) + ':alpha=1' if b_roll.fade_in_seconds else ''}"
                    f"{',fade=t=out:st=' + self._format_time(b_roll.duration - b_roll.fade_out_seconds) + ':d=' + self._format_time(b_roll.fade_out_seconds) + ':alpha=1' if b_roll.fade_out_seconds else ''},"
                    f"setpts=PTS-STARTPTS+{self._format_time(b_roll.timeline_start)}/TB[{broll_label}]"
                )
                filters.append(
                    f"[{current_video_label}][{broll_label}]overlay=x=0:y=0:eof_action=pass:shortest=0[{overlay_label}]"
                )
                current_video_label = overlay_label

        if self.visual_hooks is not None:
            # Conservative cross-platform intersection: below top chrome, above captions,
            # and entirely left of the right-hand interaction column.
            duration = self.visual_hooks["duration"]; style = self.visual_hooks["style"]
            progress_y = .724 if self.visual_hooks["platform"] == "tiktok" else .738
            if style == "border_marquee":
                filters.append(f"[{current_video_label}]drawbox=x=iw*.055:y=ih*.12:w=iw*.675:h=ih*.585:color=0x22d3ee@0.62:t=3[hookprogress]")
            else:
                base_color, fill_color, height = ("black@0.46", "0x38bdf8@0.96", ".010") if style == "gradient_line" else ("0x0f172a@0.76", "0x2dd4bf@0.94", ".018")
                filters.append(f"[{current_video_label}]drawbox=x=iw*.055:y=ih*{progress_y:.3f}:w=iw*.675:h=ih*{height}:color={base_color}:t=fill[hookbase]")
                filters.append(f"[hookbase]drawbox=x=iw*.055:y=ih*{progress_y:.3f}:w='iw*.675*min(1,t/{duration:.6f})':h=ih*{height}:color={fill_color}:t=fill[hookprogress]")
            current_video_label = "hookprogress"
            suspense = self.visual_hooks["suspense"]
            if suspense.get("enabled"):
                start, end, text = suspense["start"], suspense["end"], suspense["text"]
                filters.append(f"[{current_video_label}]drawtext=text='{text}':fontcolor=white:fontsize=h*.036:x=(w-text_w)/2:y=h*.125:box=1:boxcolor=black@0.52:boxborderw=14:enable='between(t\\,{start:.3f}\\,{end:.3f})'[hooksuspense]")
                current_video_label = "hooksuspense"

        if self.fitness_overlay is not None:
            # Each completed rep gets a short numeric impact; the bar intentionally lives in the
            # lower third so it remains visible without obscuring a lifter's face.
            hud = self.fitness_overlay
            palette = {"impact": ("0xfbbf24@0.96", "black@0.58"), "neon": ("0x22d3ee@0.98", "0x082f49@0.66"), "minimal": ("white@0.94", "black@0.42")}[hud["style"]]
            for index, event in enumerate(hud["events"]):
                start = float(event["timeline_time"]); end = start + .68; rep = int(event["rep"]); label = f"fitnessrep{index}"
                filters.append(
                    f"[{current_video_label}]drawtext=text='{rep}':fontcolor={palette[0]}:fontsize=h*.23:x=(w-text_w)/2:y=h*.33:"
                    f"box=1:boxcolor={palette[1]}:boxborderw=20:enable='between(t\\,{start:.4f}\\,{end:.4f})'[{label}]"
                )
                current_video_label = label
            progress = min(1.0, len(hud["events"]) / int(hud["target_reps"]))
            filters.append(f"[{current_video_label}]drawbox=x=iw*.12:y=ih*.82:w=iw*.76:h=ih*.014:color=black@0.55:t=fill[fitnessbarbase]")
            filters.append(f"[fitnessbarbase]drawbox=x=iw*.12:y=ih*.82:w=iw*.76*{progress:.5f}:h=ih*.014:color={palette[0]}:t=fill[fitnessbar]")
            current_video_label = "fitnessbar"
            if hud["fatigue_time"] is not None:
                start = float(hud["fatigue_time"]); end = start + 1.0
                filters.append(f"[{current_video_label}]drawbox=x=0:y=0:w=iw:h=ih:color=red@0.78:t=22:enable='between(t\\,{start:.4f}\\,{end:.4f})'[fitnessfinale]")
                current_video_label = "fitnessfinale"

        for index, effect in enumerate(self.beat_effects):
            label, start, end = f"beateffect{index}", self._format_time(effect["time"]), self._format_time(effect["time"] + effect["duration"])
            if effect["kind"] in {"white_flash", "black_flash"}:
                color = "white@0.88" if effect["kind"] == "white_flash" else "black@0.82"
                filters.append(f"[{current_video_label}]drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=fill:enable='between(t\\,{start}\\,{end})'[{label}]")
            else:
                filters.append(f"[{current_video_label}]crop=w=iw-12:h=ih-12:x='6+if(between(t\\,{start}\\,{end})\\,4*sin(75*t)\\,0)':y='6+if(between(t\\,{start}\\,{end})\\,3*cos(92*t)\\,0)',scale=w=iw+12:h=ih+12[{label}]")
            current_video_label = label

        if self.hook_rescue is not None:
            label = "hookcolorv"
            grayscale = self.hook_rescue["grayscale_seconds"]
            fade = self.hook_rescue["color_fade_seconds"]
            # Saturation ramps from monochrome to full colour during the inserted Hook clip.
            filters.append(
                f"[{current_video_label}]hue=s='if(lt(t\\,{grayscale:.4f})\\,0\\,min(1\\,(t-{grayscale:.4f})/{fade:.4f}))':eval=frame[{label}]"
            )
            current_video_label = label

        for index, overlay in enumerate(self.profanity_overlays):
            source_label, output_label = f"censoremoji{index}", f"censoroverlay{index}"
            emoji_path = self._escape_filter_filename(overlay["emoji_path"])
            filters.append(f"movie=filename='{emoji_path}':loop=1,format=rgba,scale={overlay['size']}:-1,setpts=PTS-STARTPTS[{source_label}]")
            filters.append(f"[{current_video_label}][{source_label}]overlay=x=W*{overlay['x']:.5f}-w/2:y=H*{overlay['y']:.5f}-h/2:enable='between(t\\,{overlay['start']:.4f}\\,{overlay['end']:.4f})'[{output_label}]")
            current_video_label = output_label

        if beauty_enhancement is not None:
            for index, beauty_filter in enumerate(beauty_enhancement.ffmpeg_filters()):
                label = f"beautyv{index}"
                filters.append(f"[{current_video_label}]{beauty_filter}[{label}]")
                current_video_label = label

        if lut_path is not None and lut_intensity > 0:
            escaped_lut_path = self._escape_filter_filename(lut_path)
            if lut_intensity >= 1:
                filters.append(f"[{current_video_label}]lut3d=file='{escaped_lut_path}':interp=tetrahedral[lutv]")
            else:
                filters.append(f"[{current_video_label}]split=2[lutsource][lutbase]")
                filters.append(f"[lutsource]lut3d=file='{escaped_lut_path}':interp=tetrahedral[lutgraded]")
                filters.append(f"[lutbase][lutgraded]blend=all_expr='A*(1-{lut_intensity:.4f})+B*{lut_intensity:.4f}'[lutv]")
            current_video_label = "lutv"

        if ocio_display_lut_path is not None:
            escaped_ocio_path = self._escape_filter_filename(ocio_display_lut_path)
            filters.append(f"[{current_video_label}]lut3d=file='{escaped_ocio_path}':interp=tetrahedral[ociodisplayv]")
            current_video_label = "ociodisplayv"

        if subtitle_path is not None:
            escaped_path = self._escape_subtitle_filename(subtitle_path)
            filters.append(f"[{current_video_label}]subtitles=filename='{escaped_path}'[subtitledv]")
            current_video_label = "subtitledv"

        if watermark_input_index is not None:
            # The bundled logo uses a chroma-key background; chromakey is harmless for a future alpha PNG.
            filters.append(
                f"[{watermark_input_index}:v]format=rgba,chromakey=0x00ff00:0.08:0.10,"
                "scale=w=200:h=-2[watermark]"
            )
            filters.append(
                f"[{current_video_label}][watermark]overlay=x=W-w-32:y=H-h-32:shortest=1[watermarked]"
            )
            current_video_label = "watermarked"

        if hdr_profile is not None:
            # The OCIO display LUT must already convert ACEScct to Rec.2020 PQ/HLG.
            # zscale here sets the Rec.2020 YUV matrix/range and preserves 10/12-bit precision.
            filters.append(
                f"[{current_video_label}]zscale=matrix=bt2020nc:range=tv,format={hdr_profile.pixel_format}[hdrv]"
            )
            current_video_label = "hdrv"

        if (
            subtitle_path is not None or watermark_input_index is not None or hdr_profile is not None
            or lut_path is not None or ocio_display_lut_path is not None or effective_profile is not None
            or bool(self.screen_focus_effects) or bool(self.keyframe_animations) or bool(self.speed_curves)
            or bool(self.beat_effects) or self.hook_rescue is not None or self.visual_hooks is not None or self.fitness_overlay is not None or bool(self.profanity_overlays) or self.vertical_dual_layout is not None
            or (beauty_enhancement is not None and bool(beauty_enhancement.ffmpeg_filters()))
        ):
            filters.append(f"[{current_video_label}]null[{video_label}]")
        return ";".join(filters)

    def build_command(
        self,
        input_path: str | list[str],
        output_path: str,
        *,
        video_codec: str = "auto",
        audio_codec: str = "aac",
        subtitle_path: str | None = None,
        lut_path: str | None = None,
        lut_intensity: float = 1.0,
        ocio_display_lut_path: str | None = None,
        watermark_path: str | None = None,
        hdr_profile: HDRDeliveryProfile | None = None,
        export_profile: ExportProfile | None = None,
        motion_fps: float = 30.0,
        beauty_enhancement: BeautyEnhancement | None = None,
    ) -> list[str]:
        """Return a subprocess-ready FFmpeg command, including explicit output stream maps."""
        profile = export_profile or ExportProfile()
        input_paths = [input_path] if isinstance(input_path, str) else input_path
        if not input_paths:
            raise FiltergraphBuildError("At least one input path is required")
        max_input_index = max([self.input_index, *(segment.input_index for segment in self.segments), *(segment.input_index for segment in self.b_roll_segments), *self.transition_input_indexes.values()])
        if max_input_index >= len(input_paths):
            raise FiltergraphBuildError("Timeline input_index references an input path that was not supplied")
        command = ["ffmpeg", "-y"]
        for path in input_paths:
            command.extend(["-i", path])
        watermark_input_index = None
        if watermark_path is not None:
            watermark_input_index = len(input_paths)
            command.extend(["-loop", "1", "-i", watermark_path])
        encoder_preference = video_codec
        if hdr_profile is not None and video_codec == "auto":
            # Main12 support is not universal across NVENC generations; libx265 is deterministic.
            encoder_preference = "libx265" if hdr_profile.bit_depth == 12 else "hevc"
        encoder = resolve_video_encoder(encoder_preference)
        if hdr_profile is not None and encoder.codec not in {"hevc_nvenc", "libx265"}:
            raise FiltergraphBuildError("HDR delivery requires HEVC (hevc_nvenc or libx265)")
        return command + [
            "-filter_complex", self.build_complex_filter(
                subtitle_path=subtitle_path,
                lut_path=lut_path,
                lut_intensity=lut_intensity,
                ocio_display_lut_path=ocio_display_lut_path,
                watermark_input_index=watermark_input_index,
                hdr_profile=hdr_profile,
                export_profile=profile,
                motion_fps=motion_fps,
                beauty_enhancement=beauty_enhancement,
            ),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", encoder.codec, "-preset", encoder.preset, *encoder.extra_args,
            "-b:v", profile.video_bitrate,
            "-maxrate", profile.video_bitrate, "-bufsize", profile.video_buffer_size,
            "-pix_fmt", hdr_profile.pixel_format if hdr_profile is not None else "yuv420p",
            *(hdr_profile.metadata_args(encoder.codec) if hdr_profile is not None else []),
            "-c:a", audio_codec, "-b:a", "192k",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            output_path,
        ]


def _parse_ffmpeg_time(value: str) -> float | None:
    try:
        hours, minutes, seconds = value.strip().split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None


async def _notify_progress(callback: ProgressCallback | None, percent: int) -> None:
    if callback is None:
        return
    result = callback(percent)
    if inspect.isawaitable(result):
        await result


async def run_ffmpeg_render(
    command: list[str],
    *,
    duration_seconds: float | None = None,
    progress_callback: ProgressCallback | None = None,
    timeout_seconds: int = 60 * 60,
) -> RenderProcessResult:
    """Run FFmpeg asynchronously and parse `-progress pipe:1` output into percentage updates."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            raise RenderProcessError("Could not capture FFmpeg stdout/stderr")

        stdout_lines: list[str] = []
        stderr_chunks: list[str] = []
        last_progress = -1

        async def read_progress() -> None:
            nonlocal last_progress
            while line := await process.stdout.readline():
                decoded = line.decode("utf-8", errors="replace")
                stdout_lines.append(decoded)
                if not duration_seconds or not decoded.startswith("out_time="):
                    continue
                rendered_seconds = _parse_ffmpeg_time(decoded.partition("=")[2])
                if rendered_seconds is None:
                    continue
                percent = min(99, max(0, int(rendered_seconds / duration_seconds * 100)))
                if percent > last_progress:
                    last_progress = percent
                    await _notify_progress(progress_callback, percent)

        async def read_stderr() -> None:
            while chunk := await process.stderr.read(64 * 1024):
                stderr_chunks.append(chunk.decode("utf-8", errors="replace"))

        async def wait_for_process() -> None:
            progress_task = asyncio.create_task(read_progress())
            stderr_task = asyncio.create_task(read_stderr())
            await process.wait()
            await asyncio.gather(progress_task, stderr_task)

        await asyncio.wait_for(wait_for_process(), timeout=timeout_seconds)
    except TimeoutError as exc:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        raise RenderProcessError(f"FFmpeg render timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise RenderProcessError("FFmpeg executable is unavailable") from exc

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_chunks)
    result = RenderProcessResult(return_code=process.returncode or 0, stdout=stdout, stderr=stderr)
    if result.return_code != 0:
        raise RenderProcessError(f"FFmpeg render failed (exit {result.return_code}): {stderr[-3000:]}")
    await _notify_progress(progress_callback, 100)
    return result


if __name__ == "__main__":
    # Three confirmed ranges from a single input video.
    example_timeline = {
        "segments": [
            {"source_start": 0.0, "source_end": 4.8, "action": "keep"},
            {"source_start": 6.9, "source_end": 13.5, "action": "keep"},
            {"source_start": 15.1, "source_end": 22.2, "action": "keep"},
        ]
    }
    builder = FFmpegFiltergraphBuilder(example_timeline)
    profile = ExportProfile(resolution="1080p", aspect_ratio="9:16")
    print(builder.build_complex_filter(subtitle_path="/tmp/subtitles.srt", export_profile=profile))
    print(" ".join(builder.build_command("input.mp4", "final.mp4", subtitle_path="/tmp/subtitles.srt", export_profile=profile)))
