"""Export AI-edited multi-track timelines to FCPXML, Premiere-compatible XML and EDL."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET


class TimelineExportError(ValueError):
    pass


@dataclass(frozen=True)
class MediaReference:
    asset_id: str
    name: str
    source_path: str
    duration_seconds: float
    frame_rate: float = 30.0
    width: int = 1920
    height: int = 1080
    has_audio: bool = True

    @property
    def file_uri(self) -> str:
        if "://" in self.source_path:
            return self.source_path
        normalised = self.source_path.replace("\\", "/")
        if normalised.startswith("/"):
            return f"file://{quote(normalised)}"
        return f"file:///{quote(normalised)}"


def _fcpx_time(seconds: float, timescale: int = 24_000) -> str:
    numerator = round(max(0.0, seconds) * timescale)
    return "0s" if numerator == 0 else f"{numerator}/{timescale}s"


def _frame_count(seconds: float, fps: float) -> int:
    return max(0, round(seconds * fps))


def _timecode(frame: int, fps: int) -> str:
    hours, remainder = divmod(frame, fps * 3600)
    minutes, remainder = divmod(remainder, fps * 60)
    seconds, frames = divmod(remainder, fps)
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"


class TimelineExporter:
    """Convert our multitrack JSON into interchange formats without rendering media."""

    def __init__(
        self,
        timeline_json: dict[str, Any],
        media_references: list[MediaReference] | dict[str, MediaReference],
        *,
        project_name: str = "AI Video Editor Timeline",
        frame_rate: float = 30.0,
    ) -> None:
        self.timeline_json = timeline_json
        self.media = (
            media_references if isinstance(media_references, dict)
            else {reference.asset_id: reference for reference in media_references}
        )
        self.project_name = project_name
        self.frame_rate = frame_rate
        self.tracks = self._normalise_tracks()

    def _normalise_tracks(self) -> list[dict[str, Any]]:
        if "tracks" in self.timeline_json:
            return list(self.timeline_json["tracks"])
        source_asset_id = self.timeline_json.get("source_asset_id") or self.timeline_json.get("asset_id")
        return [{
            "type": "main_video", "z_index": 0,
            "clips": [{**clip, "source_asset_id": clip.get("source_asset_id", source_asset_id)} for clip in self.timeline_json.get("segments", [])],
        }]

    def _track_clips(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        clips = [clip for clip in track.get("clips", []) if clip.get("action", "keep") == "keep"]
        if track.get("type") != "main_video":
            return clips
        # Legacy rough-cut JSON has no timeline_start: ripple retained clips together.
        cursor = 0.0
        normalised: list[dict[str, Any]] = []
        for clip in clips:
            item = dict(clip)
            if "timeline_start" not in item:
                item["timeline_start"] = cursor
            cursor = max(cursor, float(item["timeline_start"])) + float(item["source_end"]) - float(item["source_start"])
            normalised.append(item)
        return normalised

    def _reference_for(self, clip: dict[str, Any]) -> MediaReference:
        asset_id = str(clip.get("source_asset_id") or "")
        reference = self.media.get(asset_id)
        if reference is None:
            raise TimelineExportError(f"No MediaReference supplied for source_asset_id={asset_id!r}")
        return reference

    @staticmethod
    def _lane(track: dict[str, Any]) -> int:
        track_type = track.get("type", "main_video")
        if track_type == "main_video":
            return 0
        if track_type == "audio_overlay":
            return -1
        return max(1, int(track.get("z_index", 1)))

    def _timeline_duration(self) -> float:
        maximum = 0.0
        for track in self.tracks:
            for clip in self._track_clips(track):
                start = float(clip.get("timeline_start", 0))
                maximum = max(maximum, start + float(clip["source_end"]) - float(clip["source_start"]))
        return maximum

    def export_fcpxml(self) -> str:
        """Export FCPXML 1.10 with lane-based video/audio overlays and source In/Out."""
        root = ET.Element("fcpxml", version="1.10")
        resources = ET.SubElement(root, "resources")
        format_id = "r1"
        first_reference = next(iter(self.media.values()), None)
        if first_reference is None:
            raise TimelineExportError("At least one MediaReference is required")
        frame_duration = Fraction(1 / self.frame_rate).limit_denominator(100_000)
        ET.SubElement(resources, "format", id=format_id, name="FFVideoFormat", frameDuration=f"{frame_duration.numerator}/{frame_duration.denominator}s", width=str(first_reference.width), height=str(first_reference.height))
        asset_ref_ids: dict[str, str] = {}
        for index, reference in enumerate(self.media.values(), start=1):
            ref_id = f"r{index + 1}"
            asset_ref_ids[reference.asset_id] = ref_id
            ET.SubElement(
                resources, "asset", id=ref_id, name=reference.name, src=reference.file_uri,
                start="0s", duration=_fcpx_time(reference.duration_seconds), hasVideo="1", hasAudio="1" if reference.has_audio else "0", format=format_id,
            )
        library = ET.SubElement(root, "library")
        event = ET.SubElement(library, "event", name="AI Video Editor")
        project = ET.SubElement(event, "project", name=self.project_name)
        sequence = ET.SubElement(project, "sequence", format=format_id, duration=_fcpx_time(self._timeline_duration()), tcStart="0s", tcFormat="NDF")
        spine = ET.SubElement(sequence, "spine")
        for track in self.tracks:
            lane = self._lane(track)
            for clip in self._track_clips(track):
                reference = self._reference_for(clip)
                source_start, source_end = float(clip["source_start"]), float(clip["source_end"])
                duration = source_end - source_start
                if duration <= 0:
                    continue
                attributes = {
                    "name": reference.name,
                    "ref": asset_ref_ids[reference.asset_id],
                    "offset": _fcpx_time(float(clip.get("timeline_start", 0))),
                    "start": _fcpx_time(source_start),
                    "duration": _fcpx_time(duration),
                }
                if lane:
                    attributes["lane"] = str(lane)
                if track.get("type") == "audio_overlay":
                    attributes["audioRole"] = "dialogue"
                ET.SubElement(spine, "asset-clip", attributes)
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def export_premiere_xml(self) -> str:
        """Export Final Cut Pro 7 XML (xmeml v5), importable by Premiere Pro."""
        root = ET.Element("xmeml", version="5")
        sequence = ET.SubElement(root, "sequence", id="sequence-1")
        ET.SubElement(sequence, "name").text = self.project_name
        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = str(round(self.frame_rate))
        ET.SubElement(rate, "ntsc").text = "TRUE" if abs(self.frame_rate - round(self.frame_rate)) > 0.01 else "FALSE"
        ET.SubElement(sequence, "duration").text = str(_frame_count(self._timeline_duration(), self.frame_rate))
        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")
        audio = ET.SubElement(media, "audio")
        for track_index, track in enumerate(self.tracks, start=1):
            target = audio if track.get("type") == "audio_overlay" else video
            xml_track = ET.SubElement(target, "track")
            for clip_index, clip in enumerate(self._track_clips(track), start=1):
                reference = self._reference_for(clip)
                source_start, source_end = float(clip["source_start"]), float(clip["source_end"])
                timeline_start = float(clip.get("timeline_start", 0))
                clip_item = ET.SubElement(xml_track, "clipitem", id=f"clip-{track_index}-{clip_index}")
                ET.SubElement(clip_item, "name").text = reference.name
                ET.SubElement(clip_item, "enabled").text = "TRUE"
                ET.SubElement(clip_item, "start").text = str(_frame_count(timeline_start, self.frame_rate))
                ET.SubElement(clip_item, "end").text = str(_frame_count(timeline_start + source_end - source_start, self.frame_rate))
                ET.SubElement(clip_item, "in").text = str(_frame_count(source_start, self.frame_rate))
                ET.SubElement(clip_item, "out").text = str(_frame_count(source_end, self.frame_rate))
                file_node = ET.SubElement(clip_item, "file", id=f"file-{reference.asset_id}")
                ET.SubElement(file_node, "name").text = reference.name
                ET.SubElement(file_node, "pathurl").text = reference.file_uri
                file_rate = ET.SubElement(file_node, "rate")
                ET.SubElement(file_rate, "timebase").text = str(round(reference.frame_rate))
                ET.SubElement(file_rate, "ntsc").text = "FALSE"
                ET.SubElement(file_node, "duration").text = str(_frame_count(reference.duration_seconds, reference.frame_rate))
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def export_edl(self, *, title: str | None = None) -> str:
        """Export CMX3600 EDL for the main video track; overlays remain in XML exports."""
        fps = round(self.frame_rate)
        if fps not in {24, 25, 30}:
            raise TimelineExportError("CMX3600 EDL supports integer 24, 25 or 30 fps in this exporter")
        main_track = next((track for track in self.tracks if track.get("type") == "main_video"), None)
        if main_track is None:
            raise TimelineExportError("EDL export requires a main_video track")
        lines = [f"TITLE: {title or self.project_name}", "FCM: NON-DROP FRAME"]
        for index, clip in enumerate(self._track_clips(main_track), start=1):
            reference = self._reference_for(clip)
            source_start, source_end = float(clip["source_start"]), float(clip["source_end"])
            timeline_start = float(clip.get("timeline_start", 0))
            timeline_end = timeline_start + source_end - source_start
            lines.append(
                f"{index:03d}  {reference.name[:8].upper():<8} V     C        "
                f"{_timecode(_frame_count(source_start, fps), fps)} {_timecode(_frame_count(source_end, fps), fps)} "
                f"{_timecode(_frame_count(timeline_start, fps), fps)} {_timecode(_frame_count(timeline_end, fps), fps)}"
            )
            lines.append(f"* FROM CLIP NAME: {reference.name}")
        if any(track.get("type") != "main_video" for track in self.tracks):
            lines.append("* NOTE: Overlay/audio tracks are preserved in FCPXML/Premiere XML, not CMX3600 EDL.")
        return "\n".join(lines) + "\n"

    def write(self, output_path: str | Path, *, format: str) -> Path:
        exporters = {"fcpxml": self.export_fcpxml, "premiere_xml": self.export_premiere_xml, "edl": self.export_edl}
        try:
            content = exporters[format]()
        except KeyError as exc:
            raise TimelineExportError("format must be fcpxml, premiere_xml, or edl") from exc
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
