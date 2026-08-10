#!/usr/bin/env python3
"""Create a deterministic source/golden render and seed an API-addressable Timeline."""
from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path

from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Project, SubscriptionTier, Timeline, User
from app.services.storage import upload_object


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--artifacts", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.artifacts.mkdir(parents=True, exist_ok=True)
    source, subtitles, expected = args.artifacts / "source.mp4", args.artifacts / "subtitles.srt", args.artifacts / "ground-truth.mp4"
    subtitles.write_text("1\n00:00:00,350 --> 00:00:01,550\nQA subtitle alignment\n\n2\n00:00:02,050 --> 00:00:03,150\nTransition and A/V marker\n", encoding="utf-8")
    # White flash and delayed beep both occur at 1.0 seconds. The validator checks their relative
    # position after the real render rather than trusting container timestamps alone.
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=4", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=0.08",
        "-filter_complex", "[0:v]drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,1,1.08)'[v];[2:a]adelay=1000|1000[beep];[1:a][beep]amix=inputs=2:duration=first[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(source),
    ])
    escaped_subtitles = str(subtitles).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    # Independent FFmpeg reference graph. The application renderer must retain both trim boundaries,
    # subtitle placement, colors, and the marker timing to pass the later quality gate.
    filtergraph = (
        "[0:v]trim=start=0:end=1.8,setpts=PTS-STARTPTS[v0];[0:a]atrim=start=0:end=1.8,asetpts=PTS-STARTPTS[a0];"
        "[0:v]trim=start=2.2:end=4,setpts=PTS-STARTPTS[v1];[0:a]atrim=start=2.2:end=4,asetpts=PTS-STARTPTS[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[video][audio];"
        "[video]scale=w=1920:h=1080:force_original_aspect_ratio=decrease,pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black[scaled];"
        f"[scaled]subtitles=filename='{escaped_subtitles}'[outv]"
    )
    run(["ffmpeg", "-y", "-i", str(source), "-filter_complex", filtergraph, "-map", "[outv]", "-map", "[audio]", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", str(expected)])

    identifier = uuid.uuid4().hex[:12]; source_key, subtitle_key = f"qa/{identifier}/source.mp4", f"qa/{identifier}/subtitles.srt"
    upload_object(source_key, str(source), "video/mp4"); upload_object(subtitle_key, str(subtitles), "application/x-subrip")
    db = SessionLocal()
    try:
        user = User(email=f"qa-{identifier}@example.invalid", display_name="QA Render", subscription_tier=SubscriptionTier.PRO, render_credits=100)
        db.add(user); db.flush()
        project = Project(owner_id=user.id, name="QA render contract"); db.add(project); db.flush()
        asset = MediaAsset(project_id=project.id, filename="qa-source.mp4", storage_key=source_key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", size_bytes=source.stat().st_size, duration_seconds=4, width=1280, height=720, fps=30, video_codec="h264")
        db.add(asset); db.flush()
        confirmed = {"source_asset_id": str(asset.id), "segments": [{"id": "qa-a", "source_start": 0, "source_end": 1.8, "action": "keep"}, {"id": "qa-b", "source_start": 2.2, "source_end": 4, "action": "keep"}]}
        timeline = Timeline(project_id=project.id, name="QA timeline", is_current=True, settings_json={"confirmed_timeline": confirmed, "subtitles": {"srt_key": subtitle_key}})
        db.add(timeline); db.commit()
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps({"user_id": str(user.id), "timeline_id": str(timeline.id), "expected": str(expected)}, indent=2), encoding="utf-8")
    finally:
        db.close()


if __name__ == "__main__": main()
