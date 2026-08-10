# Spatial Video export

The export pipeline derives a **virtual stereo pair** from a completed Timeline render, encodes that pair as MV-HEVC, then preserves the existing Phase 13 7.1.4 PCM channel bed. It is intentionally separate from the source `RenderJob`, so a failed or experimental stereo export never replaces the approved 2D master.

## Required inputs

1. A completed render of the same Timeline with `audio_layout: "7.1.4"` and a MOV/PCM master. The job rejects any audio stream other than 12 channels.
2. An NVIDIA worker with a custom FFmpeg whose `hevc_nvenc` help includes `multiview` and whose filters include `framepack`.
3. A macOS AVFoundation bridge for Apple metadata. Set both commands below; placeholders are shell-escaped individual arguments after template expansion.

```dotenv
SPATIAL_METADATA_WRITER_COMMAND="/opt/apple-spatial-bridge/write --input {input} --output {output} --metadata {metadata}"
SPATIAL_METADATA_VERIFIER_COMMAND="/opt/apple-spatial-bridge/verify --input {input} --output {output} --metadata {metadata}"
```

The bridge must use Apple's current spatial-media APIs to apply and validate the MV-HEVC layer IDs, left/right eye tags, baseline, horizontal field of view, and disparity adjustment. A Python/FFmpeg process must not inject undocumented QuickTime boxes and claim Apple compatibility. The task fails if the bridge is absent, if `ffprobe` does not report `hvc1`, or if the bridge verifier fails.

## Build and run

```powershell
docker compose --profile spatial build spatial-worker
docker compose --profile spatial up -d spatial-worker
docker compose exec backend alembic upgrade head
```

`backend/Dockerfile.spatial-worker` builds FFmpeg with NVENC support from source. Pin `FFMPEG_REF` and `NV_CODEC_HEADERS_REF` to revisions validated against the target GPU driver and Vision Pro test device before production deployment.

Request an export:

```http
POST /api/v1/timelines/{timeline_id}/spatial-video
Content-Type: application/json

{
  "user_id": "<uuid>",
  "source_render_job_id": "<completed-7.1.4-render-uuid>",
  "ipd_mm": 63.5,
  "horizontal_fov_degrees": 80,
  "virtual_depth_range_m": 3.0,
  "max_disparity_px": 28
}
```

## Limits and platform scope

The rendered pair is depth-guided virtual stereo. Monocular depth has no absolute metric scale and cannot reconstruct pixels that were occluded in the original camera view. IPD/FOV are therefore physically motivated virtual-camera controls, while disparity is temporally smoothed and capped for comfort; they are not a substitute for a calibrated stereo capture rig.

The MV-HEVC + Apple spatial metadata deliverable targets Apple Vision Pro. The retained independently-decodable left/right files can be repackaged for a Meta Quest application, but Quest playback/container requirements need to be validated against the exact Horizon OS player or OpenXR application; this pipeline deliberately does not label the Apple MOV as a universal Quest format.
