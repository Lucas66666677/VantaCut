import asyncio
import copy
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.core.config import settings
from app.core.progress import publish_project_status
from app.models.entities import MediaAsset, RenderJob, RenderStatus
from app.models.entities import TemplateLicenseStatus
from app.services.entitlements import requires_watermark, validate_render_entitlement
from app.services.ffmpeg_filtergraph import (
    ExportProfile,
    FFmpegFiltergraphBuilder,
    HDRDeliveryProfile,
    run_ffmpeg_render,
)
from app.services.beauty_enhancement import BeautyEnhancement
from app.services.auto_reframe import analyze_video_for_reframe, write_reframe_plan_json, write_sendcmd_file
from app.services.screen_focus import write_screen_focus_sendcmd
from app.services.film_optics import FilmOpticsSettings, render_film_optics_video
from app.services.virtual_relighting import render_virtual_relight_video, settings_from_dict
from app.services.data_charts import build_chart_overlay_command
from app.services.voice_cloning import build_voice_replacement_mix_command
from app.services.audio_description import mux_audio_description_track
from app.services.language_review_overlay import build_language_review_overlay_command, render_language_review_webm
from app.services.mechanical_ar_overlay import build_mechanical_ar_overlay_command, render_mechanical_ar_webm
from app.services.lecturas import build_freeze_dodge_command, build_pip_dodge_command
from app.services.academic import academic_delivery_command
from app.services.auto_sfx import build_auto_sfx_mix_command
from app.services.meme_gifs import build_meme_overlay_command
from app.services.hook_detector import build_hook_boom_mix_command
from app.services.profanity_filter import build_profanity_mix_command, generate_censor_sticker_png
from app.services.narration_tts import build_narration_mix_command
from app.services.audio_enhancement import build_studio_sound_mix_command
from app.services.audio_sync import build_synced_audio_replace_command
from app.services.audio_delivery import (
    build_stem_mix_command,
    mix_spatial_soundscape,
    mux_multitrack_delivery,
    normalise_media_audio,
    render_timeline_stem_files,
    run_dolby_atmos_encoder,
    run_audio_command,
)
from app.services.storage import download_object, upload_object
from app.services.forensic_provenance import (
    build_c2pa_manifest,
    embed_forensic_watermark,
    make_watermark_claims,
    sign_c2pa_asset,
)
from app.services.marketplace_security import decrypt_template_payload
from app.tasks.marketplace_tasks import settle_template_license_after_render
from app.services.omnichannel_export import publish_matrix_variant_progress
from app.worker import celery_app


def _prepare_broll_inputs(
    timeline_json: dict[str, Any],
    *,
    project_id: UUID,
    source_asset: MediaAsset,
    db,
) -> tuple[dict[str, Any], list[MediaAsset], list[MediaAsset]]:
    """Resolve all main/B-Roll assets and assign stable FFmpeg input indexes.

    Input zero remains the canonical source; generated timelines may reference a
    different source asset for every main-track clip.
    """
    render_timeline = copy.deepcopy(timeline_json)
    assets_by_id: dict[str, MediaAsset] = {str(source_asset.id): source_asset}
    ordered_assets: list[MediaAsset] = []
    broll_assets: list[MediaAsset] = []

    def assign_input(source_asset_id: object) -> tuple[MediaAsset, int]:
        source_id = str(source_asset_id)
        asset = assets_by_id.get(source_id)
        if asset is None:
            asset = db.get(MediaAsset, UUID(source_id))
            if asset is None or asset.project_id != project_id:
                raise ValueError("Timeline clip asset does not belong to this project")
            assets_by_id[source_id] = asset
            ordered_assets.append(asset)
        return asset, 0 if asset.id == source_asset.id else ordered_assets.index(asset) + 1

    for track in render_timeline.get("tracks", []):
        if track.get("type") != "main_video":
            continue
        for clip in track.get("clips", []):
            if clip.get("action", "keep") != "keep":
                continue
            if not clip.get("source_asset_id"):
                clip["source_asset_id"] = str(source_asset.id)
            _, input_index = assign_input(clip["source_asset_id"])
            clip["input_index"] = input_index
    for track in render_timeline.get("tracks", []):
        if track.get("type") != "b_roll":
            continue
        for clip in track.get("clips", []):
            if clip.get("action", "keep") != "keep":
                continue
            if not clip.get("source_asset_id"):
                raise ValueError("Every B-Roll clip requires source_asset_id")
            asset, input_index = assign_input(clip["source_asset_id"])
            clip["input_index"] = input_index
            clip["audio_enabled"] = False
            if asset not in broll_assets:
                broll_assets.append(asset)
    return render_timeline, ordered_assets, broll_assets


def _main_timeline_segments(confirmed: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = confirmed.get("tracks", [])
    if isinstance(tracks, list):
        for track in tracks:
            if track.get("type") == "main_video":
                return [dict(clip) for clip in track.get("clips", [])]
    return [dict(segment) for segment in confirmed.get("segments", [])]


def _studio_sound_windows(render_timeline: dict[str, Any], effect_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Map source clip IDs to their post-edit output positions for dry/wet replacement."""
    cursor = 0.0; windows: list[dict[str, Any]] = []
    for clip in _main_timeline_segments(render_timeline):
        if clip.get("action", "keep") != "keep":
            continue
        duration = float(clip.get("source_end", 0)) - float(clip.get("source_start", 0))
        if duration <= 0:
            continue
        output_start = float(clip.get("timeline_start", cursor)) if "timeline_start" in clip else cursor
        effect = dict(effect_map.get(str(clip.get("id", "")), {})); studio = dict(effect.get("studio_sound", {}))
        if studio.get("enhanced_audio_key") and "studio_sound" in list(effect.get("audio_effects", clip.get("audio_effects", []))):
            windows.append({"audio_key": str(studio["enhanced_audio_key"]), "timeline_start": output_start, "duration": duration, "wet_mix": float(studio.get("wet_mix", 72))})
        cursor = max(cursor, output_start) + duration
    return windows


def _asset_origin(asset: MediaAsset) -> str:
    """Never claim a source is human-captured when it arrived without provenance."""
    metadata = dict(asset.metadata_json or {})
    if metadata.get("generated"):
        return "ai_generated"
    if metadata.get("c2pa_verified"):
        return "human_or_external_verified"
    return "human_or_external_unverified"


def _render_provenance(
    *, timeline, source_asset: MediaAsset, broll_assets: list[MediaAsset], render_timeline: dict[str, Any],
    render_job: RenderJob, watermark: dict[str, Any] | None,
) -> dict[str, Any]:
    """Signed C2PA custom assertion payload; omit prompts, email and storage URLs."""
    broll_by_id = {str(asset.id): asset for asset in broll_assets}
    main_clips = [
        {
            "clip_id": str(clip.get("id", "")),
            "asset_id": str(source_asset.id),
            "origin": _asset_origin(source_asset),
            "source_start": float(clip.get("source_start", 0)),
            "source_end": float(clip.get("source_end", 0)),
        }
        for track in render_timeline.get("tracks", []) if track.get("type") == "main_video"
        for clip in track.get("clips", []) if clip.get("action", "keep") == "keep"
    ]
    broll_clips = [
        {
            "clip_id": str(clip.get("id", "")),
            "asset_id": str(clip.get("source_asset_id", "")),
            "origin": _asset_origin(broll_by_id[str(clip["source_asset_id"])]),
            "timeline_start": float(clip.get("timeline_start", 0)),
            "duration": round(float(clip.get("source_end", 0)) - float(clip.get("source_start", 0)), 3),
            "generation_type": str(dict(broll_by_id[str(clip["source_asset_id"])].metadata_json or {}).get("generation_type", "")) or None,
        }
        for track in render_timeline.get("tracks", []) if track.get("type") == "b_roll"
        for clip in track.get("clips", []) if clip.get("action", "keep") == "keep" and str(clip.get("source_asset_id", "")) in broll_by_id
    ]
    settings_json = dict(timeline.settings_json or {})
    return {
        "schema": "com.aivideo.provenance.v1",
        "project_hash": __import__("hashlib").sha256(str(timeline.project_id).encode("ascii")).hexdigest(),
        "source": {"asset_id": str(source_asset.id), "origin": _asset_origin(source_asset)},
        "components": {"main_video": main_clips, "b_roll": broll_clips},
        "ai_assisted_features": {
            "ai_generated_b_roll": any(item["origin"] == "ai_generated" for item in broll_clips),
            "auto_subtitles": bool(dict(settings_json.get("subtitles", {})).get("items")),
            "ai_color_lut": bool(dict(settings_json.get("color_lut", {})).get("lut_key")),
            "film_optics": bool(dict(settings_json.get("film_optics_master", {})).get("enabled")),
            "virtual_relighting": bool(dict(settings_json.get("virtual_relight", {})).get("enabled")),
            "audio_description": bool(dict(settings_json.get("audio_description", {})).get("audio_key")),
        },
        "forensic_watermark": watermark,
        "render_job_id": str(render_job.id),
    }


@celery_app.task(name="render.render_final_timeline")
def render_final_timeline(
    render_job_id: str,
    resolution: str = "1080p",
    aspect_ratio: str = "16:9",
    video_codec: str = "auto",
    dynamic_range: str = "sdr",
    bit_depth: int = 10,
    audio_loudness_target: str = "streaming",
    audio_layout: str = "stereo",
    container_format: str = "mp4",
    include_stem_tracks: bool = False,
    spatial_delivery: str = "channel_bed",
    matrix_batch_id: str | None = None,
    matrix_variant: str | None = None,
    virtual_timeline: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Render a confirmed timeline with optional hard subtitles and upload the final MP4."""
    db = SessionLocal()
    render_job: RenderJob | None = None
    forensic_metadata: dict[str, Any] = {}
    provenance_key: str | None = None
    try:
        render_job = db.get(RenderJob, UUID(render_job_id))
        if render_job is None:
            raise ValueError("Render job not found")
        timeline = render_job.timeline
        render_settings = dict(timeline.settings_json or {})
        marketplace_license = render_job.marketplace_license
        if marketplace_license is not None:
            if marketplace_license.status != TemplateLicenseStatus.RENDERING.value:
                raise ValueError("Marketplace template license is not in rendering state")
            private_payload = decrypt_template_payload(
                marketplace_license.marketplace_template.encrypted_payload,
                marketplace_license.template_payload_sha256,
            )
            # The worker sees private Timeline/LUT/Prompt instructions only in memory; they are never
            # copied to Timeline.settings_json, API responses, render manifests, or the browser.
            private_settings = dict(private_payload.get("render_settings", {}))
            render_settings = {**render_settings, **private_settings}
        # Matrix exports pass a cloned virtual document in the Celery message.
        # It is never written back into Timeline.settings_json.
        confirmed = copy.deepcopy(virtual_timeline) if virtual_timeline is not None else dict(render_settings.get("confirmed_timeline", {}))
        source_asset_id = confirmed.get("source_asset_id")
        subtitle_data = dict(render_settings.get("subtitles", {}))
        # libass supports the same ASS transform tags used by the Canvas preview.
        subtitle_key = subtitle_data.get("ass_key") or subtitle_data.get("srt_key")
        lut_key = dict(render_settings.get("color_lut", {})).get("lut_key")
        lut_intensity = float(dict(render_settings.get("color_lut", {})).get("intensity", 1.0))
        color_management = dict(render_settings.get("color_management", {}))
        film_optics_master = dict(render_settings.get("film_optics_master", {}))
        virtual_relight = dict(render_settings.get("virtual_relight", {}))
        audio_description = dict(render_settings.get("audio_description", {}))
        language_review = dict(render_settings.get("language_review", {}))
        beauty_enhancement = BeautyEnhancement.from_json(render_settings.get("beauty_enhancement"))
        ocio_display_lut_key = color_management.get("ocio_display_lut_key")
        if not source_asset_id:
            raise ValueError("Confirmed timeline is required before rendering")

        asset = db.get(MediaAsset, UUID(source_asset_id))
        if asset is None or asset.project_id != timeline.project_id:
            raise ValueError("Timeline source asset is invalid")

        render_job.status = RenderStatus.PROCESSING
        render_job.progress = 5
        db.commit()
        publish_project_status(
            str(timeline.project_id), progress=5, stage="render_preparing",
            message="正在準備渲染素材", job_id=str(render_job.id),
        )
        publish_matrix_variant_progress(batch_id=matrix_batch_id, variant=matrix_variant, progress=5, status="processing", message="正在準備高畫質素材")

        render_timeline, additional_assets, broll_assets = _prepare_broll_inputs(
            confirmed, project_id=timeline.project_id, source_asset=asset, db=db,
        )
        motion_keyframes = dict(render_settings.get("motion_keyframes", {}))
        if motion_keyframes:
            # Kept with the immutable confirmed JSON only for this render. The Timeline setting remains
            # the canonical editable source and clips are evaluated relative to their own in/out range.
            render_timeline["motion_keyframes"] = motion_keyframes
        speed_curves = dict(render_settings.get("speed_curves", {}))
        if speed_curves:
            # Curves are clip-local, so they travel with the resolved render timeline.
            render_timeline["speed_curves"] = speed_curves
        transition_graph = dict(render_settings.get("transition_graph", {}))
        if transition_graph:
            render_timeline["transition_graph"] = transition_graph
        hook_rescue = dict(render_settings.get("hook_rescue") or {})
        if hook_rescue.get("status") == "applied":
            render_timeline["hook_rescue"] = hook_rescue
        visual_hooks = dict(render_settings.get("visual_hooks") or {})
        if visual_hooks.get("status") == "configured":
            render_timeline["visual_hooks"] = visual_hooks
        fitness_overlay = dict(render_settings.get("fitness_overlay") or {})
        if fitness_overlay.get("status") == "completed":
            render_timeline["fitness_overlay"] = fitness_overlay
        auto_pip = dict(render_settings.get("auto_pip") or {})
        if auto_pip.get("status") == "completed":
            render_timeline["auto_pip"] = auto_pip
        talking_head_confidence = dict(render_settings.get("talking_head_confidence") or {})
        vertical_dual_layout = dict(render_settings.get("vertical_dual_layout") or {})
        if vertical_dual_layout.get("status") == "completed":
            render_timeline["vertical_dual_layout"] = vertical_dual_layout
        profanity_filter = dict(render_settings.get("profanity_filter") or {})
        profile = ExportProfile(resolution=resolution, aspect_ratio="9:16" if vertical_dual_layout.get("status") == "completed" else aspect_ratio)
        hdr_profile = None
        if dynamic_range == "hdr10":
            hdr_profile = HDRDeliveryProfile(transfer="pq", bit_depth=10, hdr10_metadata=True)
        elif dynamic_range == "hlg":
            hdr_profile = HDRDeliveryProfile(transfer="hlg", bit_depth=bit_depth, hdr10_metadata=False)
        elif dynamic_range != "sdr":
            raise ValueError("dynamic_range must be sdr, hdr10, or hlg")
        if hdr_profile is not None and not ocio_display_lut_key:
            raise ValueError("HDR render requires color_management.ocio_display_lut_key (ACEScct to Rec.2100 display LUT)")
        render_duration = sum(
            float(clip["source_end"]) - float(clip["source_start"])
            for track in render_timeline.get("tracks", [])
            if track.get("type") == "main_video"
            for clip in track.get("clips", [])
            if clip.get("action", "keep") == "keep"
        )
        if not render_duration:
            render_duration = sum(
                float(segment["source_end"]) - float(segment["source_start"])
                for segment in render_timeline.get("segments", [])
                if segment.get("action", "keep") == "keep"
            )
        validate_render_entitlement(timeline.project.owner.subscription_tier, render_duration, resolution)
        with tempfile.TemporaryDirectory(prefix=f"render-{render_job_id}-") as temp_dir:
            workdir = Path(temp_dir)
            input_video = workdir / "source.mp4"
            subtitle_file = workdir / ("subtitles.ass" if str(subtitle_key).endswith(".ass") else "subtitles.srt") if subtitle_key else None
            lut_file = workdir / "look.cube"
            ocio_display_lut_file = workdir / "ocio-display.cube"
            watermark_file = Path(__file__).resolve().parents[1] / "assets" / "watermark-logo.png"
            edited_video = workdir / "edited.mp4"
            delivery_video = workdir / f"final.{container_format}"
            gaze_correction = dict(talking_head_confidence.get("gaze_correction") or {})
            gaze_key = gaze_correction.get("output_key") if gaze_correction.get("status") == "completed" and str(gaze_correction.get("source_asset_id")) == str(asset.id) and gaze_correction.get("explicit_consent") is True else None
            download_object(str(gaze_key or asset.storage_key), str(input_video))
            input_paths = [str(input_video)]
            for index, timeline_asset in enumerate(additional_assets, start=1):
                timeline_path = workdir / f"timeline-input-{index}.mp4"
                download_object(timeline_asset.storage_key, str(timeline_path))
                input_paths.append(str(timeline_path))
            transition_specs = list(dict(render_timeline.get("transition_graph", {})).get("transitions", []))
            for index, transition in enumerate(transition_specs):
                asset_key = transition.get("render_asset_key")
                if not asset_key:
                    continue
                transition_path = workdir / f"transition-{index}.mp4"
                download_object(str(asset_key), str(transition_path))
                transition["render_input_index"] = len(input_paths)
                input_paths.append(str(transition_path))
            if subtitle_key and subtitle_file is not None:
                download_object(subtitle_key, str(subtitle_file))
            if lut_key:
                download_object(lut_key, str(lut_file))
            if ocio_display_lut_key:
                download_object(ocio_display_lut_key, str(ocio_display_lut_file))
            if profanity_filter.get("status") == "completed" and profanity_filter.get("events"):
                emoji_path = workdir / "profanity-censor.png"
                generate_censor_sticker_png(str(profanity_filter.get("emoji_style", "angry")), emoji_path)
                render_timeline["profanity_filter"] = {**profanity_filter, "emoji_path": str(emoji_path)}
            publish_project_status(
                str(timeline.project_id), progress=10, stage="rendering",
                message="正在剪輯與燒錄字幕", job_id=str(render_job.id),
            )

            auto_reframe_plan = None
            reframe_command_path = None
            screen_focus_command_path = None
            auto_reframe_config = render_settings.get("auto_reframe", False)
            auto_reframe_enabled = bool(auto_reframe_config) if not isinstance(auto_reframe_config, dict) else bool(auto_reframe_config.get("enabled", False))
            # Square output needs the same subject-aware centre crop as vertical output.
            # Matrix jobs force it without modifying a user's saved auto-reframe preference.
            virtual_canvas = dict(confirmed.get("virtual_canvas", {}))
            if aspect_ratio in {"9:16", "1:1"} and (auto_reframe_enabled or bool(virtual_canvas.get("auto_reframe", False))):
                publish_project_status(
                    str(timeline.project_id), progress=12, stage="auto_reframing",
                    message="正在追蹤講者並重構直式畫面", job_id=str(render_job.id),
                )
                options = dict(auto_reframe_config) if isinstance(auto_reframe_config, dict) else {}
                auto_reframe_plan = analyze_video_for_reframe(
                    input_video,
                    detector_stride=int(options.get("detector_stride", 2)),
                    smoothing=float(options.get("smoothing", .75)),
                    max_pan_speed_px_per_second=float(options.get("max_pan_speed_px_per_second", 720)),
                )
                reframe_command_path = str(write_sendcmd_file(auto_reframe_plan, workdir / "reframe.sendcmd"))
                write_reframe_plan_json(auto_reframe_plan, workdir / "reframe-plan.json")

            focus_effects = list(render_timeline.get("screen_focus_effects", []))
            if focus_effects:
                focus_report = dict(render_settings.get("screen_focus", {}))
                focus_width = auto_reframe_plan.crop_width if auto_reframe_plan else int(asset.width or focus_report.get("source_width", 0))
                focus_height = auto_reframe_plan.crop_height if auto_reframe_plan else int(asset.height or focus_report.get("source_height", 0))
                if focus_width < 2 or focus_height < 2:
                    raise ValueError("Screen-focus render requires valid source dimensions")
                screen_focus_command_path = str(write_screen_focus_sendcmd(
                    focus_effects, focus_width, focus_height, workdir / "screen-focus.sendcmd",
                ))

            builder = FFmpegFiltergraphBuilder(
                render_timeline,
                auto_reframe_plan=auto_reframe_plan,
                reframe_command_path=reframe_command_path,
                screen_focus_command_path=screen_focus_command_path,
            )
            command = builder.build_command(
                input_paths,
                str(edited_video),
                subtitle_path=str(subtitle_file) if subtitle_file is not None else None,
                lut_path=str(lut_file) if lut_key else None,
                lut_intensity=lut_intensity,
                ocio_display_lut_path=str(ocio_display_lut_file) if ocio_display_lut_key else None,
                watermark_path=str(watermark_file) if requires_watermark(timeline.project.owner.subscription_tier) else None,
                export_profile=profile,
                video_codec=video_codec,
                hdr_profile=hdr_profile,
                motion_fps=float(asset.fps or 30.0),
                beauty_enhancement=beauty_enhancement,
            )
            def on_render_progress(percent: int) -> None:
                # Reserve the first 10% for download/setup and the last 2% for object storage upload.
                total_progress = 10 + int(percent * 0.88)
                render_job.progress = total_progress
                publish_project_status(
                    str(timeline.project_id), progress=total_progress, stage="rendering",
                    message="正在導出影片", job_id=str(render_job.id),
                )
                publish_matrix_variant_progress(batch_id=matrix_batch_id, variant=matrix_variant, progress=total_progress, status="processing", message="正在導出影片")

            asyncio.run(run_ffmpeg_render(
                command,
                duration_seconds=sum(segment.duration for segment in builder.segments),
                progress_callback=on_render_progress,
            ))

            studio_windows = _studio_sound_windows(render_timeline, dict(render_settings.get("clip_audio_effects", {})))
            if studio_windows:
                publish_project_status(str(timeline.project_id), progress=86, stage="studio_sound_mix", message="正在依乾濕比混合錄音室人聲", job_id=str(render_job.id))
                local_studio_windows: list[dict[str, Any]] = []
                for index, window in enumerate(studio_windows):
                    local_path = workdir / f"studio-sound-{index}.wav"
                    download_object(str(window["audio_key"]), str(local_path))
                    local_studio_windows.append({**window, "local_path": str(local_path)})
                studio_mixed = workdir / "studio-sound-mixed.mp4"
                try:
                    subprocess.run(build_studio_sound_mix_command(str(edited_video), local_studio_windows, str(studio_mixed)), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Studio Sound final mix timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Studio Sound final mix failed")[-2000:]) from exc
                edited_video = studio_mixed

            completed_voice_replacements = [
                dict(item) for item in [*render_settings.get("voice_replacements", []), *render_settings.get("voice_morphs", [])]
                if isinstance(item, dict) and item.get("status") == "completed" and item.get("audio_key")
            ]
            if completed_voice_replacements:
                publish_project_status(str(timeline.project_id), progress=87, stage="voice_replacement_mix", message="正在將 AI 補錄對齊並混入音軌", job_id=str(render_job.id))
                local_replacements: list[dict[str, Any]] = []
                for index, replacement in enumerate(completed_voice_replacements):
                    local_path = workdir / f"voice-replacement-{index}.wav"
                    download_object(str(replacement["audio_key"]), str(local_path))
                    local_replacements.append({**replacement, "local_path": str(local_path)})
                replacement_mixed = workdir / "voice-replacements.mp4"
                try:
                    subprocess.run(build_voice_replacement_mix_command(str(edited_video), local_replacements, str(replacement_mixed)), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Voice replacement mixing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Voice replacement mixing failed")[-2000:]) from exc
                edited_video = replacement_mixed

            if virtual_relight.get("enabled", False):
                publish_project_status(
                    str(timeline.project_id), progress=88, stage="virtual_relighting",
                    message="正在依深度與法線套用虛擬補光", job_id=str(render_job.id),
                )
                silent_relight = workdir / "relight-silent.mp4"
                relit_video = workdir / "virtual-relight.mp4"
                relight_settings = settings_from_dict(virtual_relight)
                render_virtual_relight_video(edited_video, silent_relight, relight_settings)
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(silent_relight), "-i", str(edited_video),
                            "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264", "-preset", "fast",
                            "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(relit_video),
                        ], check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Virtual relighting muxing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Virtual relighting muxing failed")[-2000:]) from exc
                edited_video = relit_video

            if film_optics_master.get("enabled", False):
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="film_optics_master",
                    message="正在套用底片乳劑與手動鏡頭 Master Layer", job_id=str(render_job.id),
                )
                silent_optics = workdir / "film-optics-silent.mp4"
                optics_video = workdir / "film-optics-master.mp4"
                render_film_optics_video(edited_video, silent_optics, FilmOpticsSettings(**film_optics_master))
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(silent_optics), "-i", str(edited_video),
                            "-map", "0:v:0", "-map", "1:a?", "-c:v", "libx264", "-preset", "fast",
                            "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(optics_video),
                        ], check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Film optics master render timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Film optics master render failed")[-2000:]) from exc
                edited_video = optics_video

            completed_charts = [
                dict(item) for item in render_settings.get("data_chart_overlays", [])
                if isinstance(item, dict) and item.get("status") == "completed" and item.get("rgba_video_key")
            ]
            if completed_charts:
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="chart_compositing",
                    message="正在以透明向量圖表合成數據圖層", job_id=str(render_job.id),
                )
                local_charts: list[dict[str, Any]] = []
                for index, chart in enumerate(completed_charts):
                    local_chart = workdir / f"chart-{index}.mov"
                    download_object(str(chart["rgba_video_key"]), str(local_chart))
                    local_charts.append({"local_path": str(local_chart), "start_time": chart["start_time"], "x": chart.get("x", .04), "y": chart.get("y", .06)})
                composited = workdir / "data-charts-lossless.mkv"
                try:
                    subprocess.run(
                        build_chart_overlay_command(str(edited_video), local_charts, str(composited)),
                        check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Data-chart compositing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Data-chart compositing failed")[-2000:]) from exc
                edited_video = composited

            completed_finance_tracks = [
                dict(item) for item in render_settings.get("finance_tracks", [])
                if isinstance(item, dict) and item.get("status") == "completed" and item.get("rgba_video_key")
            ]
            if completed_finance_tracks:
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="finance_chart_compositing",
                    message="正在合成金融 K 線、技術指標與支撐壓力線", job_id=str(render_job.id),
                )
                local_finance: list[dict[str, Any]] = []
                for index, finance_track in enumerate(completed_finance_tracks):
                    local_chart = workdir / f"finance-{index}.mov"
                    download_object(str(finance_track["rgba_video_key"]), str(local_chart))
                    local_finance.append({"local_path": str(local_chart), "start_time": finance_track["start_time"], "x": finance_track.get("x", .04), "y": finance_track.get("y", .06)})
                finance_composited = workdir / "finance-charts-lossless.mkv"
                try:
                    subprocess.run(
                        build_chart_overlay_command(str(edited_video), local_finance, str(finance_composited)),
                        check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Finance-chart compositing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Finance-chart compositing failed")[-2000:]) from exc
                edited_video = finance_composited

            meme_gif = dict(render_settings.get("meme_gif", {}))
            ready_memes = [
                dict(item) for item in meme_gif.get("events", [])
                if isinstance(item, dict) and item.get("status") == "ready" and item.get("webm_key")
            ]
            if meme_gif.get("status") == "completed" and ready_memes:
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="meme_gif_compositing",
                    message="正在插入可審閱的迷因 GIF 效果", job_id=str(render_job.id),
                )
                local_memes: list[dict[str, Any]] = []
                for index, event in enumerate(ready_memes):
                    local_path = workdir / f"meme-gif-{index}.webm"
                    download_object(str(event["webm_key"]), str(local_path))
                    local_memes.append({**event, "local_path": str(local_path)})
                meme_video = workdir / "meme-gifs.mp4"
                width, height = profile.dimensions()
                try:
                    subprocess.run(
                        build_meme_overlay_command(
                            video_path=str(edited_video), overlays=local_memes,
                            output_path=str(meme_video), width=width, height=height,
                        ),
                        check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Meme GIF compositing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Meme GIF compositing failed")[-2000:]) from exc
                edited_video = meme_video

            if language_review.get("status") == "completed" and isinstance(language_review.get("overlays"), list) and language_review.get("overlays"):
                publish_project_status(str(timeline.project_id), progress=89, stage="language_review_overlay", message="正在燒錄文法修正與進階同義詞教學圖層", job_id=str(render_job.id))
                overlay_webm, reviewed_video = workdir / "language-review.webm", workdir / "language-review.mp4"
                overlay_width, overlay_height = profile.dimensions()
                render_language_review_webm(list(language_review["overlays"]), overlay_webm, duration=sum(segment.duration for segment in builder.segments), width=overlay_width, height=overlay_height)
                try:
                    subprocess.run(build_language_review_overlay_command(str(edited_video), str(overlay_webm), str(reviewed_video)), check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Language-review overlay compositing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Language-review overlay compositing failed")[-2000:]) from exc
                edited_video = reviewed_video

            mechanical_ar = dict(render_settings.get("mechanical_ar", {}))
            if mechanical_ar.get("status") == "completed" and isinstance(mechanical_ar.get("effects"), list) and mechanical_ar.get("effects"):
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="mechanical_ar_overlay",
                    message="正在繪製機械作動、推論訊號流與程式碼高亮圖層", job_id=str(render_job.id),
                )
                ar_overlay, ar_video = workdir / "mechanical-ar.webm", workdir / "mechanical-ar.mp4"
                ar_width, ar_height = profile.dimensions()
                render_mechanical_ar_webm(list(mechanical_ar["effects"]), ar_overlay, duration=sum(segment.duration for segment in builder.segments), width=ar_width, height=ar_height)
                try:
                    subprocess.run(build_mechanical_ar_overlay_command(str(edited_video), str(ar_overlay), str(ar_video)), check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Mechanical AR overlay render timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Mechanical AR overlay render failed")[-2000:]) from exc
                edited_video = ar_video

            lecturas = dict(render_settings.get("lecturas", {}))
            if lecturas.get("status") == "completed" and isinstance(lecturas.get("interventions"), list) and lecturas.get("interventions"):
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="lecturas_dodging",
                    message="正在插入 Lecturas 雙主持問答與智慧畫面避讓", job_id=str(render_job.id),
                )
                cumulative_freeze = 0.0
                for index, intervention in enumerate(sorted((dict(item) for item in lecturas["interventions"] if isinstance(item, dict)), key=lambda item: float(item.get("anchor_output_time", 0)))):
                    assistant_asset = db.get(MediaAsset, UUID(str(intervention["asset_id"])))
                    if assistant_asset is None or assistant_asset.project_id != timeline.project_id:
                        raise ValueError("Lecturas assistant media asset is unavailable")
                    assistant_path, dodged_video = workdir / f"lecturas-{index}.mov", workdir / f"lecturas-dodged-{index}.mp4"
                    download_object(assistant_asset.storage_key, str(assistant_path))
                    duration = float(intervention["duration_seconds"])
                    mode = str(intervention.get("presentation_mode", "freeze"))
                    anchor = float(intervention["anchor_output_time"])
                    try:
                        command = build_freeze_dodge_command(str(edited_video), str(assistant_path), str(dodged_video), freeze_at=anchor + cumulative_freeze, duration=duration) if mode == "freeze" else build_pip_dodge_command(str(edited_video), str(assistant_path), str(dodged_video), start=anchor + cumulative_freeze, duration=duration)
                        subprocess.run(command, check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("Lecturas smart-timeline dodge timed out") from exc
                    except subprocess.CalledProcessError as exc:
                        raise RuntimeError((exc.stderr or "Lecturas smart-timeline dodge failed")[-2000:]) from exc
                    if mode == "freeze":
                        cumulative_freeze += duration
                    edited_video = dodged_video

            watermark_claims = None
            if settings.forensic_watermark_enabled:
                publish_project_status(
                    str(timeline.project_id), progress=89, stage="forensic_watermarking",
                    message="正在注入可驗證的頻域數位基因", job_id=str(render_job.id),
                )
                watermark_claims = make_watermark_claims(
                    user_id=timeline.project.owner_id, project_id=timeline.project_id, render_job_id=render_job.id,
                )
                watermarked_video = workdir / "forensic-watermarked.mkv"
                report = embed_forensic_watermark(
                    edited_video, watermarked_video, watermark_claims,
                    progress_callback=lambda percent: publish_project_status(
                        str(timeline.project_id), progress=89 + int(percent * 0.01), stage="forensic_watermarking",
                        message="正在注入可驗證的頻域數位基因", job_id=str(render_job.id),
                    ),
                )
                edited_video = watermarked_video
                forensic_metadata["watermark"] = {
                    "algorithm": "dct-chroma-qim-aesgcm-v1",
                    "project_hash": watermark_claims.project_hash,
                    "issued_at": watermark_claims.issued_at,
                    **report.__dict__,
                }

            if audio_loudness_target not in {"broadcast", "streaming"}:
                raise ValueError("audio_loudness_target must be broadcast or streaming")
            if audio_layout not in {"stereo", "5.1", "7.1.4"}:
                raise ValueError("audio_layout must be stereo, 5.1, or 7.1.4")
            if container_format not in {"mp4", "mov"}:
                raise ValueError("container_format must be mp4 or mov")
            if spatial_delivery not in {"channel_bed", "dolby_atmos"}:
                raise ValueError("spatial_delivery must be channel_bed or dolby_atmos")
            if audio_description.get("status") == "completed" and audio_description.get("audio_key") and spatial_delivery == "dolby_atmos":
                raise ValueError("Audio Description + Dolby Atmos packaging is not yet available; use channel_bed delivery")
            if audio_layout == "7.1.4" and container_format != "mov":
                raise ValueError("7.1.4 channel-bed delivery requires MOV; Dolby Atmos encoding requires Dolby tooling")
            publish_project_status(
                str(timeline.project_id), progress=90, stage="audio_loudness_measurement",
                message="正在量測整條時間軸的 LUFS", job_id=str(render_job.id),
            )
            stem_mix = dict(render_settings.get("stem_mix", {}))
            stem_paths: dict[str, str] | None = None
            segments: list[dict[str, Any]] = []
            stem_settings: dict[str, dict[str, Any]] = {}
            audio_mix_input = edited_video
            one_click = dict(render_settings.get("one_click", {}))
            beat_montage = dict(render_settings.get("beat_sync_montage", {}))
            auto_narrative = dict(render_settings.get("auto_narrative", {}))
            auto_sfx = dict(render_settings.get("auto_sfx", {}))
            meme_gif = dict(render_settings.get("meme_gif", {}))
            smart_audio_remix = dict(render_settings.get("smart_audio_remix", {}))
            audio_sync = dict(render_settings.get("audio_sync", {}))
            if audio_sync.get("status") == "completed":
                sync_clip = dict(audio_sync.get("audio_clip") or {}); external_id = audio_sync.get("external_audio_asset_id")
                external_asset = db.get(MediaAsset, UUID(str(external_id))) if external_id else None
                if external_asset is None or external_asset.project_id != timeline.project_id:
                    raise ValueError("Synchronized external audio asset is invalid")
                external_path = workdir / "synchronized-external-audio"; synced_audio_video = workdir / "synced-external-audio.mp4"
                download_object(external_asset.audio_key or external_asset.storage_key, str(external_path))
                publish_project_status(str(timeline.project_id), progress=91, stage="audio_sync_replace", message="正在以高音質音軌取代原始收音", job_id=str(render_job.id))
                try:
                    subprocess.run(build_synced_audio_replace_command(video_path=str(audio_mix_input), external_audio_path=str(external_path), output_path=str(synced_audio_video), offset_seconds=float(audio_sync.get("offset_seconds", 0)), timeline_segments=[dict(item) for item in audio_sync.get("segments", []) if isinstance(item, dict)]), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("External audio synchronization mix timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "External audio synchronization mix failed")[-2000:]) from exc
                audio_mix_input = synced_audio_video
            bgm_asset_id = one_click.get("bgm_asset_id") or beat_montage.get("bgm_asset_id") or auto_narrative.get("bgm_asset_id")
            remix_key = smart_audio_remix.get("remixed_audio_key") if smart_audio_remix.get("status") == "completed" else None
            has_isolated_bgm_mix = bool(remix_key) or (auto_sfx.get("status") == "configured" and auto_sfx.get("bgm_asset_id")) or (meme_gif.get("status") == "completed" and meme_gif.get("bgm_asset_id"))
            if (bgm_asset_id or remix_key) and not has_isolated_bgm_mix:
                bgm_path = workdir / "timeline-bgm"
                bgm_mixed = workdir / "timeline-bgm-mixed.mp4"
                if remix_key:
                    download_object(str(remix_key), str(bgm_path))
                else:
                    bgm_asset = db.get(MediaAsset, UUID(str(bgm_asset_id)))
                    if bgm_asset is None or bgm_asset.project_id != timeline.project_id:
                        raise ValueError("Configured BGM asset is invalid")
                    download_object(bgm_asset.audio_key or bgm_asset.storage_key, str(bgm_path))
                level_source = one_click if one_click.get("bgm_asset_id") else beat_montage if beat_montage.get("bgm_asset_id") else auto_narrative
                level = min(.8, max(0.0, float(smart_audio_remix.get("mix_level", level_source.get("bgm_mix_level", .16)))))
                start_offset = max(0.0, float(level_source.get("bgm_start_offset", 0.0)))
                label = "智慧重混 BGM" if remix_key else "AI 旁白 Lo-Fi BGM" if auto_narrative.get("bgm_asset_id") and not one_click.get("bgm_asset_id") and not beat_montage.get("bgm_asset_id") else "一鍵卡點 BGM" if beat_montage.get("bgm_asset_id") and not one_click.get("bgm_asset_id") else "模板 BGM"
                publish_project_status(str(timeline.project_id), progress=92, stage="one_click_bgm_mix", message=f"正在混合{label}", job_id=str(render_job.id))
                try:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", str(audio_mix_input), "-stream_loop", "-1", "-i", str(bgm_path),
                        "-filter_complex", f"[1:a]atrim=start={start_offset:.6f},asetpts=PTS-STARTPTS,volume={level:.4f}[bgm];[0:a][bgm]amix=inputs=2:duration=first:normalize=0[mix]",
                        "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(bgm_mixed),
                    ], check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("One-click BGM mixing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "One-click BGM mixing failed")[-2000:]) from exc
                audio_mix_input = bgm_mixed
            if stem_mix.get("status") == "configured":
                if str(stem_mix.get("source_asset_id")) != str(asset.id):
                    raise ValueError("Configured stems belong to a different source asset")
                stem_metadata = dict((asset.metadata_json or {}).get("stems", {}))
                if stem_metadata.get("status") != "completed":
                    raise ValueError("Configured Timeline stems are not available")
                stem_paths = {}
                for name in ("dialogue", "music", "sfx"):
                    key = stem_metadata.get(f"{name}_key")
                    if not key:
                        raise ValueError(f"Missing {name} stem object")
                    local_path = workdir / f"{name}.wav"
                    download_object(key, str(local_path))
                    stem_paths[name] = str(local_path)
                segments = _main_timeline_segments(confirmed)
                stem_settings = {name: dict(stem_mix.get(name, {})) for name in stem_paths}
                stem_mixed_video = workdir / "stem-mixed.mov"
                run_audio_command(build_stem_mix_command(
                    str(edited_video), stem_paths, segments, stem_settings, str(stem_mixed_video),
                ))
                audio_mix_input = stem_mixed_video

            if auto_sfx.get("status") == "configured" or meme_gif.get("status") == "completed" or bool(remix_key):
                local_events: list[dict[str, Any]] = []
                for index, event in enumerate(auto_sfx.get("events", [])):
                    sfx_asset = db.get(MediaAsset, UUID(str(event.get("source_asset_id"))))
                    if sfx_asset is None or sfx_asset.project_id != timeline.project_id:
                        raise ValueError("Auto-SFX event references an invalid project asset")
                    sfx_path = workdir / f"auto-sfx-{index}"
                    download_object(sfx_asset.audio_key or sfx_asset.storage_key, str(sfx_path))
                    local_events.append({**dict(event), "local_path": str(sfx_path)})
                meme_sfx_id = meme_gif.get("comedic_sfx_asset_id")
                if meme_sfx_id:
                    meme_sfx_asset = db.get(MediaAsset, UUID(str(meme_sfx_id)))
                    if meme_sfx_asset is None or meme_sfx_asset.project_id != timeline.project_id:
                        raise ValueError("Meme GIF SFX asset is invalid")
                    meme_sfx_path = workdir / "meme-comedic-sfx"
                    download_object(meme_sfx_asset.audio_key or meme_sfx_asset.storage_key, str(meme_sfx_path))
                    for index, event in enumerate(meme_gif.get("events", [])):
                        if isinstance(event, dict) and event.get("status") == "ready":
                            local_events.append({
                                "id": f"meme-sfx-{index}", "kind": "comedic_stinger",
                                "timeline_start": float(event.get("timeline_start", 0)),
                                "duration": min(.9, float(event.get("duration", .6))), "gain_db": -4.0,
                                "local_path": str(meme_sfx_path), "reason": "meme_gif",
                            })
                bgm_path = None
                auto_bgm_id = auto_sfx.get("bgm_asset_id") or meme_gif.get("bgm_asset_id")
                if remix_key:
                    local_bgm = workdir / "smart-remix-bgm"; download_object(str(remix_key), str(local_bgm)); bgm_path = str(local_bgm)
                elif auto_bgm_id:
                    bgm_asset = db.get(MediaAsset, UUID(str(auto_bgm_id)))
                    if bgm_asset is None or bgm_asset.project_id != timeline.project_id:
                        raise ValueError("Auto-SFX BGM asset is invalid")
                    local_bgm = workdir / "auto-sfx-bgm"; download_object(bgm_asset.audio_key or bgm_asset.storage_key, str(local_bgm)); bgm_path = str(local_bgm)
                if local_events or bgm_path:
                    auto_sfx_mixed = workdir / "auto-sfx-mixed.mp4"
                    publish_project_status(str(timeline.project_id), progress=93, stage="auto_sfx_mix", message="正在對齊音效並自動閃避 BGM", job_id=str(render_job.id))
                    try:
                        subprocess.run(build_auto_sfx_mix_command(
                            video_path=str(audio_mix_input), output_path=str(auto_sfx_mixed), sfx_events=local_events,
                            bgm_path=bgm_path, bgm_volume=float(smart_audio_remix.get("mix_level", auto_sfx.get("bgm_volume", .16))), ducking=dict(auto_sfx.get("ducking", {})),
                            tape_stop_events=[dict(item) for item in meme_gif.get("tape_stop_events", []) if isinstance(item, dict)],
                        ), check=True, capture_output=True, text=True, timeout=60 * 60)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("Auto-SFX mixing timed out") from exc
                    except subprocess.CalledProcessError as exc:
                        raise RuntimeError((exc.stderr or "Auto-SFX mixing failed")[-2000:]) from exc
                    audio_mix_input = auto_sfx_mixed

            if profanity_filter.get("status") == "completed" and profanity_filter.get("events"):
                profanity_mixed = workdir / "profanity-censored.mp4"
                publish_project_status(str(timeline.project_id), progress=94, stage="profanity_audio_mix", message="正在消音敏感詞並加入趣味音效", job_id=str(render_job.id))
                try:
                    subprocess.run(build_profanity_mix_command(video_path=str(audio_mix_input), output_path=str(profanity_mixed), events=[dict(item) for item in profanity_filter["events"]], style=str(profanity_filter.get("sfx_style", "beep"))), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Profanity audio replacement timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Profanity audio replacement failed")[-2000:]) from exc
                audio_mix_input = profanity_mixed

            if hook_rescue.get("status") == "applied" and dict(hook_rescue.get("synthetic_boom") or {}):
                boom = dict(hook_rescue["synthetic_boom"])
                hook_boom_mixed = workdir / "hook-boom-mixed.mp4"
                publish_project_status(str(timeline.project_id), progress=94, stage="hook_boom_mix", message="正在加入黃金 Hook 重低音", job_id=str(render_job.id))
                try:
                    subprocess.run(build_hook_boom_mix_command(
                        video_path=str(audio_mix_input), output_path=str(hook_boom_mixed),
                        timeline_start=float(boom.get("timeline_start", .38)), duration=float(boom.get("duration", .45)),
                        frequency_hz=float(boom.get("frequency_hz", 54)),
                    ), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Hook boom mixing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Hook boom mixing failed")[-2000:]) from exc
                audio_mix_input = hook_boom_mixed

            if fitness_overlay.get("status") == "completed" and dict(fitness_overlay.get("bass_hit") or {}):
                boom = dict(fitness_overlay["bass_hit"]); fitness_boom_mixed = workdir / "fitness-bass-hit.mp4"
                publish_project_status(str(timeline.project_id), progress=94, stage="fitness_bass_mix", message="正在加入力竭高光重低音", job_id=str(render_job.id))
                try:
                    subprocess.run(build_hook_boom_mix_command(video_path=str(audio_mix_input), output_path=str(fitness_boom_mixed), timeline_start=float(boom.get("timeline_start", 0)), duration=float(boom.get("duration", .55)), frequency_hz=float(boom.get("frequency_hz", 48))), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Fitness bass mixing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Fitness bass mixing failed")[-2000:]) from exc
                audio_mix_input = fitness_boom_mixed

            completed_narrations = [dict(item) for item in render_settings.get("tts_narrations", []) if isinstance(item, dict) and item.get("status") == "completed" and item.get("audio_key")]
            if completed_narrations:
                local_narrations: list[dict[str, Any]] = []
                for index, narration in enumerate(completed_narrations):
                    narration_path = workdir / f"tts-narration-{index}.wav"
                    download_object(str(narration["audio_key"]), str(narration_path))
                    local_narrations.append({"local_path": str(narration_path), "start_time": float(narration.get("timeline_start", 0))})
                narration_mixed = workdir / "tts-narrations-mixed.mp4"
                publish_project_status(str(timeline.project_id), progress=93, stage="tts_narration_mix", message="正在混合 AI 旁白軌", job_id=str(render_job.id))
                try:
                    subprocess.run(build_narration_mix_command(video_path=str(audio_mix_input), output_path=str(narration_mixed), narrations=local_narrations), check=True, capture_output=True, text=True, timeout=60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Narration mixing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Narration mixing failed")[-2000:]) from exc
                audio_mix_input = narration_mixed

            soundscape = dict(render_settings.get("soundscape", {}))
            has_spatial_soundscape = soundscape.get("status") == "completed"
            if has_spatial_soundscape:
                soundscape_layout = soundscape.get("layout")
                soundscape_key = soundscape.get("spatial_mix_key")
                if audio_layout not in {"5.1", "7.1.4"} or soundscape_layout != audio_layout or not soundscape_key:
                    raise ValueError("Regenerate the soundscape with the same 5.1 or 7.1.4 layout selected for export")
                soundscape_path = workdir / f"soundscape-{audio_layout.replace('.', '_')}.wav"
                spatial_mix_video = workdir / "spatial-soundscape-mix.mov"
                download_object(str(soundscape_key), str(soundscape_path))
                publish_project_status(str(timeline.project_id), progress=94, stage="spatial_audio_mix", message="正在混合空間環境音與原始對白", job_id=str(render_job.id))
                mix_spatial_soundscape(str(audio_mix_input), str(soundscape_path), str(spatial_mix_video), layout=audio_layout)  # type: ignore[arg-type]
                audio_mix_input = spatial_mix_video

            academic_delivery = dict(render_settings.get("academic_mode", {})).get("delivery", {})
            if isinstance(academic_delivery, dict) and academic_delivery.get("enabled", False):
                publish_project_status(str(timeline.project_id), progress=94, stage="academic_delivery", message="正在套用沉穩學術語速與語音動態處理", job_id=str(render_job.id))
                academic_video = workdir / "academic-delivery.mp4"
                try:
                    subprocess.run(academic_delivery_command(str(audio_mix_input), str(academic_video), tempo=float(academic_delivery.get("tempo", .96))), check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Academic delivery processing timed out") from exc
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError((exc.stderr or "Academic delivery processing failed")[-2000:]) from exc
                audio_mix_input = academic_video

            loudness_measurement = normalise_media_audio(
                str(audio_mix_input), str(delivery_video), target=audio_loudness_target,
                layout=audio_layout, container=container_format, already_spatial=has_spatial_soundscape,
            )
            if audio_description.get("status") == "completed" and audio_description.get("audio_key"):
                publish_project_status(
                    str(timeline.project_id), progress=95, stage="audio_description_mux",
                    message="正在封裝可切換口述影像音軌並套用背景音閃避", job_id=str(render_job.id),
                )
                description_audio = workdir / "audio-description.wav"
                accessible_video = workdir / f"final-audio-described.{container_format}"
                download_object(str(audio_description["audio_key"]), str(description_audio))
                mux_audio_description_track(
                    video_path=str(delivery_video), description_audio_path=str(description_audio), output_path=str(accessible_video),
                    language=str(audio_description.get("language", "und")), container=container_format,
                )
                delivery_video = accessible_video
            if include_stem_tracks:
                if audio_layout == "7.1.4":
                    raise ValueError("Editable stem packaging is unavailable for 7.1.4 channel-bed delivery")
                if stem_paths is None:
                    raise ValueError("Stem tracks require a configured stem mix")
                timeline_stems = render_timeline_stem_files(
                    stem_paths, segments, workdir / "timeline-stems", stem_settings,
                )
                packaged_video = workdir / f"final-multitrack.{container_format}"
                mux_multitrack_delivery(
                    str(delivery_video), timeline_stems, str(packaged_video), container=container_format,
                )
                delivery_video = packaged_video

            if spatial_delivery == "dolby_atmos":
                adm_key = soundscape.get("dolby_adm_bwf_key")
                if not adm_key:
                    raise ValueError("Dolby Atmos export requires a Dolby-authored ADM BWF key in timeline soundscape settings")
                adm_bwf = workdir / "atmos-master.adm.wav"
                atmos_output = workdir / f"final-atmos.{container_format}"
                download_object(str(adm_key), str(adm_bwf))
                publish_project_status(str(timeline.project_id), progress=96, stage="dolby_atmos_encoding", message="正在交由 Dolby 編碼器封裝 Atmos", job_id=str(render_job.id))
                run_dolby_atmos_encoder(str(delivery_video), str(adm_bwf), str(atmos_output))
                delivery_video = atmos_output

            provenance = _render_provenance(
                timeline=timeline,
                source_asset=asset,
                broll_assets=broll_assets,
                render_timeline=render_timeline,
                render_job=render_job,
                watermark=forensic_metadata.get("watermark"),
            )
            provenance_file = workdir / "provenance.json"
            provenance_file.write_text(__import__("json").dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
            provenance_key = f"projects/{timeline.project_id}/renders/{render_job.id}/provenance.json"
            upload_object(provenance_key, str(provenance_file), "application/json")
            if settings.c2pa_enabled:
                publish_project_status(
                    str(timeline.project_id), progress=97, stage="c2pa_signing",
                    message="正在簽署 C2PA 內容溯源憑證", job_id=str(render_job.id),
                )
                c2pa_video = workdir / f"final-c2pa.{container_format}"
                signed = sign_c2pa_asset(
                    delivery_video,
                    c2pa_video,
                    build_c2pa_manifest(title=timeline.project.name, provenance=provenance),
                )
                delivery_video = c2pa_video
                forensic_metadata["c2pa"] = {
                    "status": "signed_and_verified",
                    "claim_generator": settings.c2pa_claim_generator,
                    "verification_report": signed["verification_report"],
                }
            else:
                forensic_metadata["c2pa"] = {"status": "disabled"}

            output_key = f"projects/{timeline.project_id}/renders/{render_job.id}/final-{resolution}-{aspect_ratio.replace(':', 'x')}-{dynamic_range}-{audio_loudness_target}.{container_format}"
            publish_project_status(
                str(timeline.project_id), progress=98, stage="render_uploading",
                message="正在上傳導出檔案", job_id=str(render_job.id),
            )
            upload_object(output_key, str(delivery_video), "video/quicktime" if container_format == "mov" else "video/mp4")
            timeline.settings_json = {
                **dict(timeline.settings_json or {}),
                "audio_delivery": {
                    "target": audio_loudness_target,
                    "layout": audio_layout,
                    "container": container_format,
                    "include_stem_tracks": include_stem_tracks,
                    "spatial_delivery": spatial_delivery,
                    "measurement": loudness_measurement,
                    "audio_description": bool(audio_description.get("status") == "completed" and audio_description.get("audio_key")),
                },
            }

        render_job.status = RenderStatus.COMPLETED
        render_job.progress = 100
        render_job.output_key = output_key
        render_job.output_format = container_format
        render_job.error_message = None
        render_job.provenance_key = provenance_key
        render_job.forensic_metadata_json = forensic_metadata
        db.commit()
        publish_project_status(
            str(timeline.project_id), progress=100, stage="render_completed", status="completed",
            message="影片導出完成", job_id=str(render_job.id),
        )
        publish_matrix_variant_progress(batch_id=matrix_batch_id, variant=matrix_variant, progress=100, status="completed", message="此比例已完成")
        if render_job.marketplace_license is not None:
            # Payment settlement is deliberately decoupled: a queue retry can safely repeat it.
            try:
                settle_template_license_after_render.delay(str(render_job.id))
            except Exception:
                # The reconciler picks this up from the committed COMPLETED render state. Never
                # downgrade a delivered video solely because the settlement queue is unavailable.
                pass
        return {"render_job_id": str(render_job.id), "output_key": output_key, "status": render_job.status.value}
    except Exception as exc:
        db.rollback()
        if render_job is not None:
            current = db.get(RenderJob, render_job.id)
            if current is not None:
                current.status = RenderStatus.FAILED
                current.error_message = str(exc)
                db.commit()
                publish_project_status(
                    str(current.project_id), progress=current.progress, stage="render_failed", status="failed",
                    message=str(exc), job_id=str(current.id),
                )
                publish_matrix_variant_progress(batch_id=matrix_batch_id, variant=matrix_variant, progress=current.progress, status="failed", message=str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="render.bundle_omnichannel_exports")
def bundle_omnichannel_exports(variant_results: list[dict[str, str]], timeline_id: str, batch_id: str) -> dict[str, str]:
    """Collect completed variants from object storage into one portable ZIP artifact."""
    del variant_results  # RenderJob rows are authoritative even after Celery retries.
    db = SessionLocal()
    try:
        timeline = db.get(__import__("app.models.entities", fromlist=["Timeline"]).Timeline, UUID(timeline_id))
        if timeline is None:
            raise ValueError("Timeline not found")
        settings_json = dict(timeline.settings_json or {})
        batches = dict(settings_json.get("omnichannel_export_batches", {}))
        batch = dict(batches.get(batch_id, {}))
        variants = list(batch.get("variants", []))
        if len(variants) != 3:
            raise ValueError("Matrix batch has invalid variants")
        jobs = [db.get(RenderJob, UUID(str(item["render_job_id"]))) for item in variants]
        if any(job is None or job.status != RenderStatus.COMPLETED or not job.output_key for job in jobs):
            raise ValueError("Cannot bundle incomplete matrix renders")
        with tempfile.TemporaryDirectory(prefix=f"matrix-{batch_id}-") as temp_dir:
            workdir = Path(temp_dir); archive_path = workdir / "omnichannel-export.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for item, job in zip(variants, jobs, strict=True):
                    extension = str(job.output_format or "mp4")
                    local = workdir / f"{item['key']}.{extension}"
                    download_object(str(job.output_key), str(local))
                    archive.write(local, arcname=f"{item['key']}-{item['aspect_ratio'].replace(':', 'x')}.{extension}")
            zip_key = f"projects/{timeline.project_id}/exports/matrix/{batch_id}/omnichannel-export.zip"
            upload_object(zip_key, str(archive_path), "application/zip")
        batch.update({"status": "completed", "zip_status": "completed", "zip_key": zip_key})
        batches[batch_id] = batch; settings_json["omnichannel_export_batches"] = batches; timeline.settings_json = settings_json
        db.commit()
        return {"batch_id": batch_id, "zip_key": zip_key, "status": "completed"}
    except Exception:
        db.rollback()
        timeline = db.get(__import__("app.models.entities", fromlist=["Timeline"]).Timeline, UUID(timeline_id))
        if timeline is not None:
            settings_json = dict(timeline.settings_json or {}); batches = dict(settings_json.get("omnichannel_export_batches", {})); batch = dict(batches.get(batch_id, {}))
            batch.update({"status": "failed", "zip_status": "failed"}); batches[batch_id] = batch; settings_json["omnichannel_export_batches"] = batches; timeline.settings_json = settings_json; db.commit()
        raise
    finally:
        db.close()
