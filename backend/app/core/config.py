import os


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    environment: str = os.getenv("ENVIRONMENT", "development").lower()
    mock_ai: bool = _as_bool(os.getenv("MOCK_AI", "false"))
    mock_ai_delay_seconds: float = float(os.getenv("MOCK_AI_DELAY_SECONDS", "0.35"))
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
    s3_public_endpoint_url: str = os.getenv("S3_PUBLIC_ENDPOINT_URL", s3_endpoint_url)
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "minioadmin123")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_bucket: str = os.getenv("S3_BUCKET", "media")
    presigned_url_expire_seconds: int = int(os.getenv("PRESIGNED_URL_EXPIRE_SECONDS", "900"))
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ocio_config_path: str | None = os.getenv("OCIO_CONFIG_PATH")
    gaze_redirection_onnx_path: str | None = os.getenv("GAZE_REDIRECTION_ONNX_PATH")
    video_inpainting_provider: str = os.getenv("VIDEO_INPAINTING_PROVIDER", "propainter")
    propainter_command: str | None = os.getenv("PROPAINTER_COMMAND")
    social_oauth_redirect_base_url: str = os.getenv("SOCIAL_OAUTH_REDIRECT_BASE_URL", "http://localhost:8000")
    social_token_encryption_key: str | None = os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY")
    youtube_client_id: str | None = os.getenv("YOUTUBE_CLIENT_ID")
    youtube_client_secret: str | None = os.getenv("YOUTUBE_CLIENT_SECRET")
    tiktok_client_key: str | None = os.getenv("TIKTOK_CLIENT_KEY")
    tiktok_client_secret: str | None = os.getenv("TIKTOK_CLIENT_SECRET")
    thumbnail_generation_command: str | None = os.getenv("THUMBNAIL_GENERATION_COMMAND")
    screen_focus_ocr_lang: str = os.getenv("SCREEN_FOCUS_OCR_LANG", "eng")
    screen_focus_cursor_templates: tuple[str, ...] = tuple(
        item.strip() for item in os.getenv("SCREEN_FOCUS_CURSOR_TEMPLATES", "").split(",") if item.strip()
    )
    # Pre-provision this YOLO-World weight on Workers; do not allow background jobs to download arbitrary models.
    mechanical_yolo_world_model: str | None = os.getenv("MECHANICAL_YOLO_WORLD_MODEL")
    mechanical_detection_confidence: float = float(os.getenv("MECHANICAL_DETECTION_CONFIDENCE", "0.28"))
    mechanical_max_sampled_frames: int = int(os.getenv("MECHANICAL_MAX_SAMPLED_FRAMES", "900"))
    depth_anything_onnx_path: str | None = os.getenv("DEPTH_ANYTHING_ONNX_PATH")
    lottie_render_command: str | None = os.getenv("LOTTIE_RENDER_COMMAND")
    tts_command: str | None = os.getenv("TTS_COMMAND")
    # A provider-approved Suno/Udio gateway; never target undocumented consumer endpoints.
    music_generation_timeout_seconds: int = int(os.getenv("MUSIC_GENERATION_TIMEOUT_SECONDS", "1800"))
    music_generation_poll_seconds: float = float(os.getenv("MUSIC_GENERATION_POLL_SECONDS", "4"))
    music_stem_separator_command: str = os.getenv("MUSIC_STEM_SEPARATOR_COMMAND", "spleeter")
    # Neutral narrator command (for example Piper); never reuse a creator voice profile.
    # It must contain {text} and {output}; {language} is optional.
    audio_description_tts_command: str | None = os.getenv("AUDIO_DESCRIPTION_TTS_COMMAND", os.getenv("TTS_COMMAND"))
    audio_description_words_per_second: float = float(os.getenv("AUDIO_DESCRIPTION_WORDS_PER_SECOND", "2.2"))
    audio_description_tts_timeout_seconds: int = int(os.getenv("AUDIO_DESCRIPTION_TTS_TIMEOUT_SECONDS", "300"))
    kinetic_subtitle_webm: bool = _as_bool(os.getenv("KINETIC_SUBTITLE_WEBM", "false"))
    sam2_checkpoint_path: str | None = os.getenv("SAM2_CHECKPOINT_PATH")
    sam2_config_path: str = os.getenv("SAM2_CONFIG_PATH", "configs/sam2.1/sam2.1_hiera_l.yaml")
    matting_frame_stride: int = int(os.getenv("MATTING_FRAME_STRIDE", "1"))
    voice_profile_encryption_key: str | None = os.getenv("VOICE_PROFILE_ENCRYPTION_KEY")
    xtts_model_dir: str | None = os.getenv("XTTS_MODEL_DIR")
    xtts_config_path: str | None = os.getenv("XTTS_CONFIG_PATH")
    xtts_device: str = os.getenv("XTTS_DEVICE", "cuda")
    xtts_use_deepspeed: bool = _as_bool(os.getenv("XTTS_USE_DEEPSPEED", "false"))
    # Worker-local RVC-compatible command. It must consume the original audio
    # plus the extracted prosody JSON and preserve timing in its output WAV.
    # Required placeholders: {input}, {output}, {model}, {f0_json}, {envelope_json}.
    rvc_convert_command: str | None = os.getenv("RVC_CONVERT_COMMAND")
    rvc_robot_model_path: str | None = os.getenv("RVC_ROBOT_MODEL_PATH")
    rvc_monster_model_path: str | None = os.getenv("RVC_MONSTER_MODEL_PATH")
    rvc_storybook_model_path: str | None = os.getenv("RVC_STORYBOOK_MODEL_PATH")
    rvc_timeout_seconds: int = int(os.getenv("RVC_TIMEOUT_SECONDS", "1800"))
    ffmpeg_gltransition_enabled: bool = _as_bool(os.getenv("FFMPEG_GLTRANSITION_ENABLED", "false"))
    wav2lip_command: str | None = os.getenv("WAV2LIP_COMMAND")
    sadtalker_command: str | None = os.getenv("SADTALKER_COMMAND")
    wav2lip_commercial_licensed: bool = _as_bool(os.getenv("WAV2LIP_COMMERCIAL_LICENSED", "false"))
    video_generation_provider: str = os.getenv("VIDEO_GENERATION_PROVIDER", "sora")
    video_generation_timeout_seconds: int = int(os.getenv("VIDEO_GENERATION_TIMEOUT_SECONDS", "1800"))
    video_outpaint_provider: str = os.getenv("VIDEO_OUTPAINT_PROVIDER", "svd_cli")
    svd_outpaint_command: str | None = os.getenv("SVD_OUTPAINT_COMMAND")
    broll_max_visual_motion: float = float(os.getenv("BROLL_MAX_VISUAL_MOTION", "0.055"))
    # Server-side Mapbox v6 geocoding. `permanent=true` is required before route
    # coordinates are retained in Timeline settings; check the Mapbox contract.
    mapbox_access_token: str | None = os.getenv("MAPBOX_ACCESS_TOKEN")
    mapbox_geocoding_permanent: bool = _as_bool(os.getenv("MAPBOX_GEOCODING_PERMANENT", "false"))
    travel_map_geocoding_timeout_seconds: int = int(os.getenv("TRAVEL_MAP_GEOCODING_TIMEOUT_SECONDS", "20"))
    travel_map_render_timeout_seconds: int = int(os.getenv("TRAVEL_MAP_RENDER_TIMEOUT_SECONDS", "300"))
    # Stock B-Roll is opt-in and server-side only; never expose this key to the browser.
    pexels_api_key: str | None = os.getenv("PEXELS_API_KEY")
    stock_broll_download_max_bytes: int = int(os.getenv("STOCK_BROLL_DOWNLOAD_MAX_BYTES", str(300 * 1024 * 1024)))
    stock_broll_download_timeout_seconds: int = int(os.getenv("STOCK_BROLL_DOWNLOAD_TIMEOUT_SECONDS", "180"))
    # Meme/GIF search remains opt-in. Keep vendor credentials on workers only and
    # retain the provider/source URL in Timeline settings for attribution review.
    meme_gif_provider: str = os.getenv("MEME_GIF_PROVIDER", "tenor").lower()
    tenor_api_key: str | None = os.getenv("TENOR_API_KEY")
    tenor_client_key: str = os.getenv("TENOR_CLIENT_KEY", "ai-video-editor")
    giphy_api_key: str | None = os.getenv("GIPHY_API_KEY")
    meme_gif_max_events: int = int(os.getenv("MEME_GIF_MAX_EVENTS", "5"))
    meme_gif_download_max_bytes: int = int(os.getenv("MEME_GIF_DOWNLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
    meme_gif_timeout_seconds: int = int(os.getenv("MEME_GIF_TIMEOUT_SECONDS", "30"))
    # Optional worker-local AI commands. They must accept {input} and {output} placeholders
    # and write a mono/stereo WAV; RNNoise, DeepFilterNet or a licensed equivalent can be used.
    studio_sound_ai_command: str | None = os.getenv("STUDIO_SOUND_AI_COMMAND")
    studio_sound_timeout_seconds: int = int(os.getenv("STUDIO_SOUND_TIMEOUT_SECONDS", "900"))
    colmap_command: str = os.getenv("COLMAP_COMMAND", "colmap")
    three_dgs_train_command: str | None = os.getenv("THREE_DGS_TRAIN_COMMAND")
    three_dgs_render_command: str | None = os.getenv("THREE_DGS_RENDER_COMMAND")
    spatial_training_timeout_seconds: int = int(os.getenv("SPATIAL_TRAINING_TIMEOUT_SECONDS", str(8 * 60 * 60)))
    spatial_render_timeout_seconds: int = int(os.getenv("SPATIAL_RENDER_TIMEOUT_SECONDS", str(2 * 60 * 60)))
    # Live director: MediaMTX handles protocol ingress, while aiortc handles the
    # low-latency program bus and direct mobile WebRTC publisher offers.
    live_mediamtx_internal_rtsp_base_url: str = os.getenv("LIVE_MEDIAMTX_INTERNAL_RTSP_BASE_URL", "rtsp://mediamtx:8554")
    live_mediamtx_public_rtmp_base_url: str = os.getenv("LIVE_MEDIAMTX_PUBLIC_RTMP_BASE_URL", "rtmp://localhost:1935")
    live_vad_threshold: float = float(os.getenv("LIVE_VAD_THRESHOLD", "0.62"))
    live_min_switch_seconds: float = float(os.getenv("LIVE_MIN_SWITCH_SECONDS", "0.8"))
    live_video_codec: str = os.getenv("LIVE_VIDEO_CODEC", "libx264")
    live_captions_enabled: bool = _as_bool(os.getenv("LIVE_CAPTIONS_ENABLED", "true"))
    live_caption_window_seconds: float = float(os.getenv("LIVE_CAPTION_WINDOW_SECONDS", "2.0"))
    live_caption_ttl_seconds: float = float(os.getenv("LIVE_CAPTION_TTL_SECONDS", "2.8"))
    silero_vad_torchscript_path: str | None = os.getenv("SILERO_VAD_TORCHSCRIPT_PATH")
    # Render-node-only forensic controls. Keep the AES key in a secret manager;
    # the C2PA signer must be a KMS/HSM-aware subprocess, never a PEM in env vars.
    forensic_watermark_enabled: bool = _as_bool(os.getenv("FORENSIC_WATERMARK_ENABLED", "false"))
    watermark_encryption_key: str | None = os.getenv("WATERMARK_ENCRYPTION_KEY")
    watermark_frame_stride: int = max(1, int(os.getenv("WATERMARK_FRAME_STRIDE", "15")))
    watermark_dct_strength: float = float(os.getenv("WATERMARK_DCT_STRENGTH", "18"))
    watermark_min_copies: int = max(1, int(os.getenv("WATERMARK_MIN_COPIES", "3")))
    watermark_max_copies: int = max(1, int(os.getenv("WATERMARK_MAX_COPIES", "7")))
    c2pa_enabled: bool = _as_bool(os.getenv("C2PA_ENABLED", "false"))
    c2patool_command: str | None = os.getenv("C2PATOOL_COMMAND")
    c2pa_signer_path: str | None = os.getenv("C2PA_SIGNER_PATH")
    c2pa_claim_generator: str = os.getenv("C2PA_CLAIM_GENERATOR", "AI Video Editor/1.0")
    c2pa_timeout_seconds: int = int(os.getenv("C2PA_TIMEOUT_SECONDS", "300"))
    # Public Platform API: use independent secrets from application/session keys.
    platform_api_key_pepper: str | None = os.getenv("PLATFORM_API_KEY_PEPPER")
    platform_webhook_encryption_key: str | None = os.getenv("PLATFORM_WEBHOOK_ENCRYPTION_KEY")
    platform_redis_key_prefix: str = os.getenv("PLATFORM_REDIS_KEY_PREFIX", "platform:rate")
    platform_max_source_bytes: int = int(os.getenv("PLATFORM_MAX_SOURCE_BYTES", str(5 * 1024 * 1024 * 1024)))
    platform_allow_private_source_urls: bool = _as_bool(os.getenv("PLATFORM_ALLOW_PRIVATE_SOURCE_URLS", "false"))
    platform_webhook_timeout_seconds: int = int(os.getenv("PLATFORM_WEBHOOK_TIMEOUT_SECONDS", "15"))
    platform_management_token: str | None = os.getenv("PLATFORM_MANAGEMENT_TOKEN")
    # Camera-to-cloud ingest: terminate TLS at the edge, then retain a signed
    # request envelope at the application layer to prevent replay/tampering.
    ingest_device_encryption_key: str | None = os.getenv("INGEST_DEVICE_ENCRYPTION_KEY")
    ingest_management_token: str | None = os.getenv("INGEST_MANAGEMENT_TOKEN")
    ingest_require_tls: bool = _as_bool(os.getenv("INGEST_REQUIRE_TLS", "true"))
    ingest_max_chunk_bytes: int = int(os.getenv("INGEST_MAX_CHUNK_BYTES", str(2 * 1024 * 1024 * 1024)))
    ingest_signature_max_age_seconds: int = int(os.getenv("INGEST_SIGNATURE_MAX_AGE_SECONDS", "300"))
    ingest_replay_ttl_seconds: int = int(os.getenv("INGEST_REPLAY_TTL_SECONDS", "900"))
    # Retention inference is advisory until calibrated from consented platform analytics.
    retention_model_path: str | None = os.getenv("RETENTION_MODEL_PATH")
    retention_window_seconds: float = float(os.getenv("RETENTION_WINDOW_SECONDS", "1.0"))
    # Spatial-video worker: the FFmpeg binary must expose MV-HEVC NVENC support.
    spatial_ffmpeg_path: str = os.getenv("SPATIAL_FFMPEG_PATH", "ffmpeg")
    spatial_ffprobe_path: str = os.getenv("SPATIAL_FFPROBE_PATH", "ffprobe")
    spatial_metadata_writer_command: str | None = os.getenv("SPATIAL_METADATA_WRITER_COMMAND")
    spatial_metadata_verifier_command: str | None = os.getenv("SPATIAL_METADATA_VERIFIER_COMMAND")
    spatial_mvhevc_timeout_seconds: int = int(os.getenv("SPATIAL_MVHEVC_TIMEOUT_SECONDS", str(4 * 60 * 60)))
    # Marketplace: this key encrypts Timeline/LUT/prompt payloads independently of OAuth secrets.
    marketplace_template_encryption_key: str | None = os.getenv("MARKETPLACE_TEMPLATE_ENCRYPTION_KEY")
    marketplace_template_key_version: str = os.getenv("MARKETPLACE_TEMPLATE_KEY_VERSION", "v1")
    marketplace_currency: str = os.getenv("MARKETPLACE_CURRENCY", "usd").lower()
    marketplace_creator_share_bps: int = int(os.getenv("MARKETPLACE_CREATOR_SHARE_BPS", "7000"))
    marketplace_platform_share_bps: int = int(os.getenv("MARKETPLACE_PLATFORM_SHARE_BPS", "3000"))
    stripe_secret_key: str | None = os.getenv("STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    stripe_connect_refresh_url: str = os.getenv("STRIPE_CONNECT_REFRESH_URL", "http://localhost:3000/creator/payouts/refresh")
    stripe_connect_return_url: str = os.getenv("STRIPE_CONNECT_RETURN_URL", "http://localhost:3000/creator/payouts/complete")
    # Media asset management. Enable lifecycle writes only against a production AWS S3 bucket.
    mam_s3_lifecycle_enabled: bool = _as_bool(os.getenv("MAM_S3_LIFECYCLE_ENABLED", "false"))
    mam_archive_after_days: int = int(os.getenv("MAM_ARCHIVE_AFTER_DAYS", "30"))
    mam_restore_days: int = int(os.getenv("MAM_RESTORE_DAYS", "3"))
    mam_restore_tier: str = os.getenv("MAM_RESTORE_TIER", "Standard")
    mam_restore_eta_hours: int = int(os.getenv("MAM_RESTORE_ETA_HOURS", "12"))
    mam_free_ttl_days: int = int(os.getenv("MAM_FREE_TTL_DAYS", "90"))
    mam_notice_days: tuple[int, ...] = tuple(int(value) for value in os.getenv("MAM_NOTICE_DAYS", "60,75,85").split(","))
    sendgrid_api_key: str | None = os.getenv("SENDGRID_API_KEY")
    sendgrid_from_email: str | None = os.getenv("SENDGRID_FROM_EMAIL")
    web_app_base_url: str = os.getenv("WEB_APP_BASE_URL", "http://localhost:3000")
    # Mobile handoff links are capability URLs. Set a dedicated high-entropy value in production.
    mobile_handoff_token_secret: str = os.getenv("MOBILE_HANDOFF_TOKEN_SECRET", os.getenv("S3_SECRET_KEY", "minioadmin123"))
    mobile_handoff_ttl_seconds: int = int(os.getenv("MOBILE_HANDOFF_TTL_SECONDS", "900"))
    cloud_draft_max_bytes: int = int(os.getenv("CLOUD_DRAFT_MAX_BYTES", str(5 * 1024 * 1024)))
    # Optional consented facial-action-unit model. Absence means visual coaching excludes FACS claims.
    facs_onnx_path: str | None = os.getenv("FACS_ONNX_PATH")
    facs_input_size: int = int(os.getenv("FACS_INPUT_SIZE", "224"))
    avatar_audio_provider: str = os.getenv("AVATAR_AUDIO_PROVIDER", "mock")
    audio2face_gateway_url: str | None = os.getenv("AUDIO2FACE_GATEWAY_URL")
    avatar_unreal_command: str | None = os.getenv("AVATAR_UNREAL_COMMAND")
    avatar_unreal_project: str | None = os.getenv("AVATAR_UNREAL_PROJECT")
    avatar_render_timeout_seconds: int = int(os.getenv("AVATAR_RENDER_TIMEOUT_SECONDS", str(2 * 60 * 60)))
    # Community compute is opt-in only. Use a secret-manager value in production
    # to sign short-lived assignment tickets; never issue bare storage credentials.
    distributed_compute_tracker_key: str | None = os.getenv("DISTRIBUTED_COMPUTE_TRACKER_KEY")
    distributed_compute_ticket_ttl_seconds: int = int(os.getenv("DISTRIBUTED_COMPUTE_TICKET_TTL_SECONDS", "900"))
    distributed_compute_credit_per_verified_chunk: int = int(os.getenv("DISTRIBUTED_COMPUTE_CREDIT_PER_VERIFIED_CHUNK", "1"))
    distributed_compute_max_browser_resolution: int = int(os.getenv("DISTRIBUTED_COMPUTE_MAX_BROWSER_RESOLUTION", "1080"))
    finance_provider: str = os.getenv("FINANCE_PROVIDER", "twse").lower()
    finance_cache_ttl_seconds: int = int(os.getenv("FINANCE_CACHE_TTL_SECONDS", "900"))
    # Provider contracts vary; keep licensed vendor data out of Redis unless explicitly allowed.
    finance_yahoo_cache_allowed: bool = os.getenv("FINANCE_YAHOO_CACHE_ALLOWED", "false").lower() == "true"
    finance_twse_timeout_seconds: int = int(os.getenv("FINANCE_TWSE_TIMEOUT_SECONDS", "20"))
    # Licensed/contracted Yahoo-compatible endpoint only; no browser key exposure or undocumented scraping.
    finance_yahoo_compatible_base_url: str | None = os.getenv("FINANCE_YAHOO_COMPATIBLE_BASE_URL")
    finance_yahoo_compatible_api_key: str | None = os.getenv("FINANCE_YAHOO_COMPATIBLE_API_KEY")

    @property
    def use_mock_ai(self) -> bool:
        return self.mock_ai or self.environment in {"development", "test"}


settings = Settings()
