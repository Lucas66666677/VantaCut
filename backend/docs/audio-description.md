# Auto Audio Description

`POST /api/v1/timelines/{timeline_id}/generate-audio-description` queues accessible narration after rough-cut audio analysis and a confirmed Timeline are available. The worker finds retained dialogue-free intervals, uses the vision provider with a strict JSON ceiling, synthesizes a neutral narrator, and stores a full-length aligned WAV in object storage.

Configure `AUDIO_DESCRIPTION_TTS_COMMAND` for a production TTS runtime such as Piper; it must contain `{text}` and `{output}`. Development mock mode intentionally creates a non-speech cue, so it is safe for UI work but must never be shipped as narration.

The final MP4/MOV has two selectable audio tracks: `Original Mix` (default) and `Audio Description`. The latter sidechain-ducks the programme mix only while narration is present. Dolby Atmos packaging is deliberately rejected when this initial two-track accessibility path is requested, rather than silently dropping the description track.
