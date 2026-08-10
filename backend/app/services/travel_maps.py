"""Geocode an itinerary and package a small, render-safe route animation.

Mapbox credentials are intentionally used only on the worker.  The generated
MP4 is a normal project MediaAsset, so it follows the existing B-Roll renderer.
"""
from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings


class TravelMapError(RuntimeError):
    pass


@dataclass(frozen=True)
class MapPoint:
    label: str
    longitude: float
    latitude: float

    def public_json(self) -> dict[str, object]:
        return {"label": self.label, "longitude": round(self.longitude, 5), "latitude": round(self.latitude, 5)}


_PLACE_PATTERNS = (
    re.compile(r"(?:出發去|前往|飛往|抵達|到達|去)([^，。！？!？、;；\n]{2,40})"),
    re.compile(r"(?:from|to|arrive in|depart for)\s+([A-Za-z][A-Za-z .'-]{1,50})", re.IGNORECASE),
)


def extract_place_mentions(text: str, *, max_places: int = 5) -> list[str]:
    """Explainable first-pass place extraction for narration and manually typed routes."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    explicit = [
        re.sub(r"^(?:出發去|前往|飛往|抵達|到達|去|from|to)\s*", "", part, flags=re.IGNORECASE).strip()
        for part in re.split(r"(?:->|→)", compact)
        if 1 < len(part.strip()) <= 60
    ]
    candidates: list[str] = explicit if ("->" in compact or "→" in compact) else []
    for pattern in _PLACE_PATTERNS:
        candidates.extend(match.group(1).strip(" ：:,.，。") for match in pattern.finditer(compact))
    clean: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"^(?:了|的|我們|我|再|然後)", "", candidate).strip()
        # Prevent a full narration sentence from becoming a costly geocode query.
        candidate = re.split(r"(?:然後|之後|，|。|!|！|\?|？)", candidate)[0].strip()
        if len(candidate) < 2 or candidate in clean:
            continue
        clean.append(candidate)
        if len(clean) >= max_places:
            break
    return clean


class MapboxGeocoder:
    endpoint = "https://api.mapbox.com/search/geocode/v6/forward"

    def geocode(self, query: str) -> MapPoint:
        if not settings.mapbox_access_token:
            raise TravelMapError("MAPBOX_ACCESS_TOKEN is required for travel route generation")
        if not settings.mapbox_geocoding_permanent:
            raise TravelMapError("Set MAPBOX_GEOCODING_PERMANENT=true before retaining route coordinates in a project timeline")
        try:
            response = httpx.get(
                self.endpoint,
                params={
                    "q": query,
                    "access_token": settings.mapbox_access_token,
                    "limit": 1,
                    "autocomplete": "false",
                    "permanent": str(settings.mapbox_geocoding_permanent).lower(),
                },
                timeout=settings.travel_map_geocoding_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TravelMapError(f"Mapbox geocoding failed for '{query}': {exc}") from exc
        features = list(response.json().get("features", []))
        if not features:
            raise TravelMapError(f"Mapbox could not find '{query}'")
        feature = dict(features[0])
        coordinates = feature.get("geometry", {}).get("coordinates") or feature.get("properties", {}).get("coordinates", {}).get("longitude_latitude")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
            raise TravelMapError(f"Mapbox returned no coordinates for '{query}'")
        try:
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError) as exc:
            raise TravelMapError(f"Mapbox returned invalid coordinates for '{query}'") from exc
        label = str(feature.get("name") or feature.get("properties", {}).get("name") or query)
        return MapPoint(label=label, longitude=longitude, latitude=latitude)


def route_points_from_text(text: str) -> list[MapPoint]:
    places = extract_place_mentions(text)
    if len(places) < 2:
        raise TravelMapError("請在旁白或輸入文字中提供至少兩個地點，例如「出發去雷克雅維克，抵達台北」")
    geocoder = MapboxGeocoder()
    return [geocoder.geocode(place) for place in places]


def _project(points: list[MapPoint], width: int, height: int) -> list[tuple[float, float]]:
    longitudes, latitudes = [item.longitude for item in points], [item.latitude for item in points]
    min_x, max_x = min(longitudes), max(longitudes); min_y, max_y = min(latitudes), max(latitudes)
    span_x, span_y = max(.01, max_x - min_x), max(.01, max_y - min_y)
    margin_x, margin_y = width * .16, height * .18
    return [
        (margin_x + (item.longitude - min_x) / span_x * (width - margin_x * 2), height - margin_y - (item.latitude - min_y) / span_y * (height - margin_y * 2))
        for item in points
    ]


def _bezier(start: tuple[float, float], end: tuple[float, float], steps: int = 80) -> list[tuple[float, float]]:
    sx, sy = start; ex, ey = end
    control = ((sx + ex) / 2, min(sy, ey) - abs(ex - sx) * .22 - 38)
    return [((1-t)**2*sx + 2*(1-t)*t*control[0] + t*t*ex, (1-t)**2*sy + 2*(1-t)*t*control[1] + t*t*ey) for t in (index / steps for index in range(steps + 1))]


def _route_curve(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    curve: list[tuple[float, float]] = []
    for start, end in zip(points, points[1:]):
        curve.extend(_bezier(start, end)[1 if curve else 0:])
    return curve


def _draw_vehicle(draw: ImageDraw.ImageDraw, x: float, y: float, angle: float, vehicle: str) -> None:
    size = 22 if vehicle == "plane" else 18
    if vehicle == "plane":
        points = [(size, 0), (-size * .65, -size * .42), (-size * .3, 0), (-size * .65, size * .42)]
    else:
        points = [(size, -size*.45), (size*.55, size*.45), (-size*.7, size*.45), (-size, 0), (-size*.7, -size*.45), (size*.55, -size*.45)]
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    transformed = [(x + px * cos_a - py * sin_a, y + px * sin_a + py * cos_a) for px, py in points]
    draw.polygon(transformed, fill="#fbbf24", outline="#fff7d6", width=3)


def render_route_map_video(*, points: list[MapPoint], output_path: Path, duration_seconds: float, aspect_ratio: str, vehicle: str) -> tuple[int, int]:
    """Draw a branded map animation into raw frames and encode it with FFmpeg.

    Keeping this server implementation free of map tiles avoids exposing a
    browser token in exported media while the WebGL editor provides live preview.
    """
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    fps = 30; frame_count = max(1, int(duration_seconds * fps)); projected = _project(points, width, height); curve = _route_curve(projected)
    font = ImageFont.load_default()
    command = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        for frame_index in range(frame_count):
            image = Image.new("RGB", (width, height), "#071120"); draw = ImageDraw.Draw(image)
            # Minimal map-like grid: exported assets remain visually clear without tile licensing/attribution concerns.
            for x in range(0, width, 48): draw.line((x, 0, x, height), fill="#10223a", width=1)
            for y in range(0, height, 48): draw.line((0, y, width, y), fill="#10223a", width=1)
            draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=28, outline="#27496d", width=2)
            revealed = max(2, int(len(curve) * (frame_index + 1) / frame_count))
            for index in range(0, revealed - 1, 3):
                if (index // 3) % 2 == 0:
                    draw.line((curve[index], curve[min(index + 2, revealed - 1)]), fill="#60a5fa", width=7)
            for index, (x, y) in enumerate(projected):
                draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill="#e0f2fe", outline="#38bdf8", width=4)
                draw.text((x + 16, y - 6), points[index].label[:34], fill="#e2e8f0", font=font)
            x, y = curve[revealed - 1]; previous = curve[max(0, revealed - 3)]; _draw_vehicle(draw, x, y, math.atan2(y - previous[1], x - previous[0]), vehicle)
            process.stdin.write(np.asarray(image, dtype=np.uint8).tobytes())
        process.stdin.close(); process.stdin = None
        _, stderr = process.communicate(timeout=settings.travel_map_render_timeout_seconds)
        if process.returncode != 0:
            raise TravelMapError(f"FFmpeg could not encode route animation: {stderr.decode(errors='replace')[-1200:]}")
    except subprocess.TimeoutExpired as exc:
        process.kill(); raise TravelMapError("Route animation rendering timed out") from exc
    except FileNotFoundError as exc:
        raise TravelMapError("FFmpeg is required for travel route animation") from exc
    return width, height


def create_route_sfx(*, output_path: Path, duration_seconds: float, vehicle: str) -> None:
    """Generate a short original stinger; no third-party SFX is silently bundled."""
    source = "sine=frequency=115:sample_rate=48000" if vehicle == "plane" else "sine=frequency=360:sample_rate=48000"
    command = ["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-t", f"{min(duration_seconds, 1.2):.3f}", "-af", "volume=0.12,afade=t=in:d=0.04,afade=t=out:st=0.7:d=0.35", "-c:a", "pcm_s16le", str(output_path)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise TravelMapError("Could not synthesize the travel route SFX") from exc
