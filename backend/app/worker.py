import os

from celery.schedules import crontab

from celery import Celery

celery_app = Celery(
    "video_editor",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    include=[
        "app.tasks.media_tasks",
        "app.tasks.audio_tasks",
        "app.tasks.final_cut_tasks",
        "app.tasks.education_tasks",
        "app.tasks.language_review_tasks",
        "app.tasks.subtitle_tasks",
        "app.tasks.bgm_tasks",
        "app.tasks.render_tasks",
        "app.tasks.audio_enhancement_tasks",
        "app.tasks.embedding_tasks",
        "app.tasks.gaming_highlight_tasks",
        "app.tasks.color_tasks",
        "app.tasks.agent_tasks",
        "app.tasks.stem_tasks",
        "app.tasks.optical_tasks",
        "app.tasks.speaker_tasks",
        "app.tasks.inpainting_tasks",
        "app.tasks.soundscape_tasks",
        "app.tasks.social_tasks",
        "app.tasks.screen_focus_tasks",
        "app.tasks.relighting_tasks",
        "app.tasks.data_chart_tasks",
        "app.tasks.auto_director_tasks",
        "app.tasks.matting_tasks",
        "app.tasks.parallax_tasks",
        "app.tasks.voice_tasks",
        "app.tasks.transition_tasks",
        "app.tasks.localization_tasks",
        "app.tasks.beat_sync_tasks",
        "app.tasks.video_generation_tasks",
        "app.tasks.spatial_tasks",
        "app.tasks.platform_tasks",
        "app.tasks.ingest_tasks",
        "app.tasks.spatial_video_tasks",
        "app.tasks.marketplace_tasks",
        "app.tasks.mam_tasks",
        "app.tasks.behavioral_coach_tasks",
        "app.tasks.avatar_tasks",
        "app.tasks.audio_description_tasks",
        "app.tasks.distributed_compute_tasks",
        "app.tasks.finance_tasks",
        "app.tasks.mechanical_ar_tasks",
        "app.tasks.lecturas_tasks",
        "app.tasks.academic_tasks",
        "app.tasks.one_click_tasks",
        "app.tasks.narration_tasks",
        "app.tasks.stock_broll_tasks",
        "app.tasks.profanity_tasks",
        "app.tasks.auto_narrative_tasks",
        "app.tasks.vertical_dual_layout_tasks",
        "app.tasks.meme_gif_tasks",
        "app.tasks.smart_audio_remix_tasks",
        "app.tasks.long_to_shorts_tasks",
        "app.tasks.travel_map_tasks",
        "app.tasks.fitness_overlay_tasks",
        "app.tasks.audio_sync_tasks",
        "app.tasks.text_to_music_tasks",
        "app.tasks.auto_pip_tasks",
        "app.tasks.spatial_text_tasks",
    ],
)

# Keep long-running GPU renders isolated from latency-sensitive AI/media tasks.
render_queue = os.getenv("CELERY_RENDER_QUEUE", "render")
spatial_queue = os.getenv("CELERY_SPATIAL_QUEUE", "spatial")
inpainting_queue = os.getenv("CELERY_INPAINTING_QUEUE", "celery")
celery_app.conf.task_routes = {
    "render.*": {"queue": render_queue},
    # Keep local development runnable on the default worker; production can route this to a GPU pool.
    "video.inpaint_selected_object": {"queue": inpainting_queue},
    "matting.generate_video_matte": {"queue": inpainting_queue},
    "parallax.generate_layers": {"queue": inpainting_queue},
    "voice.extract_profile": {"queue": inpainting_queue},
    "voice.generate_replacement": {"queue": inpainting_queue},
    "voice.generate_morph": {"queue": inpainting_queue},
    "transition.build_asset": {"queue": inpainting_queue},
    "localization.generate_dubbed_version": {"queue": inpainting_queue},
    "video.outpaint": {"queue": inpainting_queue},
    "spatial.reconstruct_scene": {"queue": inpainting_queue},
    "spatial.render_virtual_camera": {"queue": inpainting_queue},
    "spatial_video.render_mvhevc": {"queue": spatial_queue},
    "marketplace.*": {"queue": render_queue},
    "mam.*": {"queue": render_queue},
    "analysis.analyze_behavioral_coach": {"queue": inpainting_queue},
    "avatar.*": {"queue": inpainting_queue},
    "accessibility.*": {"queue": inpainting_queue},
    "education.review_language_video": {"queue": inpainting_queue},
    "compute.*": {"queue": render_queue},
    "finance.*": {"queue": render_queue},
    "mechanical_ar.*": {"queue": inpainting_queue},
    "lecturas.*": {"queue": inpainting_queue},
    "academic.*": {"queue": inpainting_queue},
    "one_click.*": {"queue": render_queue},
    "auto_narrative.*": {"queue": render_queue},
    "vertical_layout.*": {"queue": render_queue},
    "meme_gif.*": {"queue": render_queue},
    "smart_audio_remix.*": {"queue": render_queue},
    "long_to_shorts.*": {"queue": render_queue},
    "travel_map.*": {"queue": render_queue},
    "fitness.*": {"queue": inpainting_queue},
    "audio_sync.*": {"queue": render_queue},
    "text_to_music.*": {"queue": render_queue},
    "auto_pip.*": {"queue": inpainting_queue},
    "spatial_text.*": {"queue": inpainting_queue},
}
celery_app.conf.beat_schedule = {
    "run-youtube-thumbnail-experiments-hourly": {
        "task": "social.run_thumbnail_experiments",
        "schedule": 3600.0,
    },
    "generate-platform-invoices-monthly": {
        "task": "platform.generate_monthly_invoices",
        "schedule": crontab(day_of_month="1", hour="0", minute="15"),
    },
    "reconcile-marketplace-settlements": {
        "task": "marketplace.reconcile_pending_settlements",
        "schedule": 300.0,
    },
    "configure-mam-lifecycle-daily": {"task": "mam.configure_lifecycle", "schedule": crontab(hour=0, minute=5)},
    "archive-inactive-completed-projects-daily": {"task": "mam.archive_completed_projects", "schedule": crontab(hour=1, minute=10)},
    "refresh-mam-hydration": {"task": "mam.refresh_archive_and_hydration", "schedule": 900.0},
    "send-free-storage-retention-notices-daily": {"task": "mam.send_free_tier_retention_notices", "schedule": crontab(hour=9, minute=0)},
    "purge-inactive-free-raw-assets-daily": {"task": "mam.purge_inactive_free_raw_assets", "schedule": crontab(hour=3, minute=0)},
}
if os.getenv("CELERY_BROKER_URL", "").startswith("sqs://"):
    celery_app.conf.broker_transport_options = {
        "region": os.getenv("AWS_REGION", "us-east-1"),
        "visibility_timeout": int(os.getenv("SQS_RENDER_VISIBILITY_TIMEOUT", "7200")),
        "polling_interval": float(os.getenv("SQS_POLLING_INTERVAL", "1")),
    }


@celery_app.task
def health_task() -> str:
    return "ok"
