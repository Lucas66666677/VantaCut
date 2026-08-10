from fastapi import APIRouter

from app.api.v1.media import router as media_router
from app.api.v1.ai import router as ai_router
from app.api.v1.analysis import router as analysis_router
from app.api.v1.subtitles import router as subtitles_router
from app.api.v1.project_status import router as project_status_router
from app.api.v1.audio_enhancement import router as audio_enhancement_router
from app.api.v1.renders import router as renders_router
from app.api.v1.collaboration import router as collaboration_router
from app.api.v1.agent import router as agent_router
from app.api.v1.audio_delivery import router as audio_delivery_router
from app.api.v1.optics import router as optics_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.speaker import router as speaker_router
from app.api.v1.inpainting import router as inpainting_router
from app.api.v1.film_optics import router as film_optics_router
from app.api.v1.social import router as social_router
from app.api.v1.screen_focus import router as screen_focus_router
from app.api.v1.relighting import router as relighting_router
from app.api.v1.data_charts import router as data_charts_router
from app.api.v1.auto_director import router as auto_director_router
from app.api.v1.matting import router as matting_router
from app.api.v1.keyframes import router as keyframes_router
from app.api.v1.parallax import router as parallax_router
from app.api.v1.voice import router as voice_router
from app.api.v1.transitions import router as transitions_router
from app.api.v1.localization import router as localization_router
from app.api.v1.beat_sync import montage_router as beat_sync_montage_router, router as beat_sync_router
from app.api.v1.video_generation import broll_router, outpaint_router
from app.api.v1.spatial import router as spatial_router
from app.api.v1.live import router as live_router
from app.api.v1.forensics import router as forensics_router
from app.api.v1.workspace import router as workspace_router
from app.api.v1.platform import router as platform_router
from app.api.v1.camera_ingest import router as camera_ingest_router
from app.api.v1.spatial_video import router as spatial_video_router
from app.api.v1.marketplace import router as marketplace_router
from app.api.v1.media_lifecycle import router as media_lifecycle_router
from app.api.v1.interactive import creator_router as interactive_creator_router, player_router as interactive_player_router
from app.api.v1.behavioral_coach import router as behavioral_coach_router
from app.api.v1.avatar import router as avatar_router
from app.api.v1.audio_description import router as audio_description_router
from app.api.v1.distributed_compute import router as distributed_compute_router
from app.api.v1.finance import router as finance_router
from app.api.v1.mechanical_ar import router as mechanical_ar_router
from app.api.v1.lecturas import router as lecturas_router
from app.api.v1.academic import router as academic_router
from app.api.v1.one_click import router as one_click_router
from app.api.v1.rough_cut import router as rough_cut_router
from app.api.v1.auto_reframe import router as auto_reframe_router
from app.api.v1.auto_sfx import router as auto_sfx_router
from app.api.v1.color_filters import router as color_filters_router
from app.api.v1.cloud_drafts import router as cloud_drafts_router
from app.api.v1.stickers import library_router as sticker_library_router, router as stickers_router
from app.api.v1.speed_curves import router as speed_curves_router
from app.api.v1.narrations import router as narrations_router
from app.api.v1.beauty_enhancement import router as beauty_enhancement_router
from app.api.v1.semantic_stock_broll import router as semantic_stock_broll_router
from app.api.v1.profanity import router as profanity_router
from app.api.v1.auto_narrative import router as auto_narrative_router
from app.api.v1.vertical_dual_layout import router as vertical_dual_layout_router
from app.api.v1.meme_gifs import router as meme_gif_router
from app.api.v1.smart_audio_remix import router as smart_audio_remix_router
from app.api.v1.visual_hooks import router as visual_hooks_router
from app.api.v1.long_to_shorts import router as long_to_shorts_router
from app.api.v1.travel_maps import router as travel_maps_router
from app.api.v1.fitness_overlay import router as fitness_overlay_router
from app.api.v1.talking_head import router as talking_head_router
from app.api.v1.audio_sync import router as audio_sync_router
from app.api.v1.semantic_snapping import router as semantic_snapping_router
from app.api.v1.workspace_context import router as workspace_context_router
from app.api.v1.nudge import router as nudge_router
from app.api.v1.text_to_music import router as text_to_music_router
from app.api.v1.auto_pip import router as auto_pip_router
from app.api.v1.spatial_text import router as spatial_text_router
from app.api.v1.wireless_cameras import mobile_router as wireless_camera_mobile_router, router as wireless_camera_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(media_router)
api_router.include_router(ai_router)
api_router.include_router(analysis_router)
api_router.include_router(subtitles_router)
api_router.include_router(project_status_router)
api_router.include_router(audio_enhancement_router)
api_router.include_router(renders_router)
api_router.include_router(collaboration_router)
api_router.include_router(agent_router)
api_router.include_router(audio_delivery_router)
api_router.include_router(optics_router)
api_router.include_router(reviews_router)
api_router.include_router(speaker_router)
api_router.include_router(inpainting_router)
api_router.include_router(film_optics_router)
api_router.include_router(social_router)
api_router.include_router(screen_focus_router)
api_router.include_router(relighting_router)
api_router.include_router(data_charts_router)
api_router.include_router(auto_director_router)
api_router.include_router(matting_router)
api_router.include_router(keyframes_router)
api_router.include_router(parallax_router)
api_router.include_router(voice_router)
api_router.include_router(transitions_router)
api_router.include_router(localization_router)
api_router.include_router(beat_sync_router)
api_router.include_router(beat_sync_montage_router)
api_router.include_router(broll_router)
api_router.include_router(outpaint_router)
api_router.include_router(spatial_router)
api_router.include_router(live_router)
api_router.include_router(forensics_router)
api_router.include_router(workspace_router)
api_router.include_router(platform_router)
api_router.include_router(camera_ingest_router)
api_router.include_router(spatial_video_router)
api_router.include_router(marketplace_router)
api_router.include_router(media_lifecycle_router)
api_router.include_router(interactive_creator_router)
api_router.include_router(interactive_player_router)
api_router.include_router(behavioral_coach_router)
api_router.include_router(avatar_router)
api_router.include_router(audio_description_router)
api_router.include_router(distributed_compute_router)
api_router.include_router(finance_router)
api_router.include_router(mechanical_ar_router)
api_router.include_router(lecturas_router)
api_router.include_router(academic_router)
api_router.include_router(one_click_router)
api_router.include_router(rough_cut_router)
api_router.include_router(auto_reframe_router)
api_router.include_router(auto_sfx_router)
api_router.include_router(color_filters_router)
api_router.include_router(cloud_drafts_router)
api_router.include_router(stickers_router)
api_router.include_router(sticker_library_router)
api_router.include_router(speed_curves_router)
api_router.include_router(narrations_router)
api_router.include_router(beauty_enhancement_router)
api_router.include_router(semantic_stock_broll_router)
api_router.include_router(profanity_router)
api_router.include_router(auto_narrative_router)
api_router.include_router(vertical_dual_layout_router)
api_router.include_router(meme_gif_router)
api_router.include_router(smart_audio_remix_router)
api_router.include_router(visual_hooks_router)
api_router.include_router(long_to_shorts_router)
api_router.include_router(travel_maps_router)
api_router.include_router(fitness_overlay_router)
api_router.include_router(talking_head_router)
api_router.include_router(audio_sync_router)
api_router.include_router(semantic_snapping_router)
api_router.include_router(workspace_context_router)
api_router.include_router(nudge_router)
api_router.include_router(text_to_music_router)
api_router.include_router(auto_pip_router)
api_router.include_router(spatial_text_router)
api_router.include_router(wireless_camera_router)
api_router.include_router(wireless_camera_mobile_router)
