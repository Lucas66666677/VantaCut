"""Reviewable meme trigger detection, vendor GIF retrieval, and FFmpeg composition."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class MemeGifError(RuntimeError):
    pass


KEYWORD_TRIGGERS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("傻眼", "bruh", "無言"), "confused_reaction", "confused reaction"),
    (("大崩潰", "崩潰", "完蛋"), "meltdown", "dramatic meltdown reaction"),
    (("唉", "唉呀", "唉呦", "sigh"), "sigh", "exasperated sigh reaction"),
)


def _normalise(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def detect_meme_triggers(
    *, transcript: dict[str, Any] | None, silences: list[dict[str, Any]] | None,
    min_silence_seconds: float = .8, cooldown_seconds: float = 4.5, limit: int | None = None,
) -> list[dict[str, Any]]:
    """Derive conservative, reviewable meme candidates from existing ASR/silence analysis."""
    candidates: list[dict[str, Any]] = []
    for silence in silences or []:
        start, end = float(silence.get("start", 0)), float(silence.get("end", 0))
        if end - start >= min_silence_seconds:
            candidates.append({"trigger_type": "awkward_pause", "timeline_start": start + min(.22, (end - start) / 3), "duration": min(1.3, end - start), "query": "awkward silence reaction", "reason": f"偵測到 {end - start:.1f} 秒無言停頓"})
    for segment in (transcript or {}).get("segments", []):
        for word in segment.get("words", []):
            text = _normalise(word.get("word"))
            for phrases, kind, query in KEYWORD_TRIGGERS:
                if any(phrase in text for phrase in phrases):
                    candidates.append({"trigger_type": kind, "timeline_start": float(word.get("start", segment.get("start", 0))), "duration": 1.15, "query": query, "reason": f"ASR 偵測到喜劇語氣詞「{word.get('word')}」"})
                    break
    selected: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: float(value["timeline_start"])):
        if selected and float(item["timeline_start"]) - float(selected[-1]["timeline_start"]) < cooldown_seconds:
            continue
        selected.append({**item, "id": f"meme-{len(selected) + 1}", "timeline_start": round(float(item["timeline_start"]), 3), "duration": round(float(item["duration"]), 3)})
        if len(selected) >= (limit or settings.meme_gif_max_events):
            break
    return selected


def _trusted_media_url(url: str, provider: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    allowed = ("tenor.com", "giphy.com") if provider == "auto" else (("tenor.com",) if provider == "tenor" else ("giphy.com",))
    return urlparse(url).scheme == "https" and any(host == domain or host.endswith(f".{domain}") for domain in allowed)


def search_gif(*, query: str, provider: str = "auto") -> dict[str, str]:
    """Return a provider-attributed GIF URL. Network access only occurs with an explicit server key."""
    candidates = [settings.meme_gif_provider] if provider == "auto" else [provider]
    if provider == "auto" and settings.meme_gif_provider != "giphy":
        candidates.append("giphy")
    errors: list[str] = []
    for selected in dict.fromkeys(candidates):
        try:
            if selected == "tenor":
                if not settings.tenor_api_key:
                    raise MemeGifError("TENOR_API_KEY is not configured")
                response = httpx.get("https://tenor.googleapis.com/v2/search", params={"key": settings.tenor_api_key, "client_key": settings.tenor_client_key, "q": query, "limit": 1, "media_filter": "gif", "contentfilter": "medium", "searchfilter": "sticker"}, timeout=settings.meme_gif_timeout_seconds, follow_redirects=False)
                response.raise_for_status(); result = (response.json().get("results") or [])[0]
                media = dict(result.get("media_formats") or {})
                item = media.get("tinygif") or media.get("gif") or media.get("mediumgif") or {}
                url = str(item.get("url") or "")
                if not _trusted_media_url(url, "tenor"):
                    raise MemeGifError("Tenor returned an untrusted media URL")
                return {"provider": "tenor", "query": query, "url": url, "source_url": str(result.get("itemurl") or url), "title": str(result.get("title") or query)}
            if selected == "giphy":
                if not settings.giphy_api_key:
                    raise MemeGifError("GIPHY_API_KEY is not configured")
                response = httpx.get("https://api.giphy.com/v1/gifs/search", params={"api_key": settings.giphy_api_key, "q": query, "limit": 1, "rating": "g"}, timeout=settings.meme_gif_timeout_seconds, follow_redirects=False)
                response.raise_for_status(); result = (response.json().get("data") or [])[0]
                images = dict(result.get("images") or {}); item = images.get("downsized") or images.get("original") or {}
                url = str(item.get("url") or "")
                if not _trusted_media_url(url, "giphy"):
                    raise MemeGifError("Giphy returned an untrusted media URL")
                return {"provider": "giphy", "query": query, "url": url, "source_url": str(result.get("url") or url), "title": str(result.get("title") or query)}
            raise MemeGifError(f"Unsupported GIF provider: {selected}")
        except (httpx.HTTPError, IndexError, KeyError, MemeGifError) as exc:
            errors.append(str(exc))
    raise MemeGifError("; ".join(errors) or "No GIF search provider is configured")


def download_gif(url: str, destination: Path, provider: str) -> Path:
    if not _trusted_media_url(url, provider):
        raise MemeGifError("Refusing to download an untrusted GIF URL")
    downloaded = 0
    try:
        with httpx.stream("GET", url, timeout=settings.meme_gif_timeout_seconds, follow_redirects=False) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > settings.meme_gif_download_max_bytes:
                        raise MemeGifError("GIF exceeds MEME_GIF_DOWNLOAD_MAX_BYTES")
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        raise MemeGifError(f"GIF download failed: {exc}") from exc
    return destination


def gif_to_webm(source: Path, destination: Path, duration: float) -> Path:
    """Preserve GIF alpha where present; VP9 alpha needs auto-alt-ref disabled."""
    try:
        subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source), "-t", f"{max(.1, duration):.3f}", "-vf", "fps=20,scale='min(720,iw)':-2:flags=lanczos,format=rgba", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(destination)], check=True, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise MemeGifError("GIF to WebM conversion timed out") from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise MemeGifError(f"GIF to WebM conversion failed: {detail[-1000:]}") from exc
    return destination


def build_meme_overlay_command(*, video_path: str, overlays: list[dict[str, Any]], output_path: str, width: int, height: int) -> list[str]:
    """Composite alpha stickers or full-screen cutaways while copying the program audio."""
    command = ["ffmpeg", "-y", "-i", video_path]; filters = ["[0:v]setpts=PTS-STARTPTS[meme_base]"]
    for index, overlay in enumerate(overlays, start=1):
        command.extend(["-stream_loop", "-1", "-i", str(overlay["local_path"])])
        start, duration = max(0.0, float(overlay["timeline_start"])), max(.08, float(overlay["duration"]))
        end, label, next_label = start + duration, f"meme_in_{index}", f"meme_out_{index}"
        if str(overlay.get("insertion_mode", "overlay")) == "cutaway":
            transform = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            position = "x=0:y=0"
        else:
            transform = f"scale={max(160, int(width * .46))}:-2:flags=lanczos"
            position = "x=(W-w)/2:y=H-h-{max(24, int(height * .12))}"
        filters.append(f"[{index}:v]fps=20,{transform},setpts=PTS-STARTPTS+{start:.3f}/TB,trim=duration={duration:.3f}[{label}]")
        filters.append(f"[meme_base][{label}]overlay={position}:eof_action=pass:enable='between(t\\,{start:.3f}\\,{end:.3f})'[{next_label}]")
        filters.append(f"[{next_label}]null[meme_base]")
    return command + ["-filter_complex", ";".join(filters), "-map", "[meme_base]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-c:a", "copy", "-movflags", "+faststart", output_path]
