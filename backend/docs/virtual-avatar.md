# Virtual avatar replacement

The worker creates a provider-neutral animation document: Audio2Face-compatible blendshape frames plus MediaPipe-to-IK motion frames. Set `AVATAR_AUDIO_PROVIDER=audio2face` and expose a private gateway that accepts `/blendshapes`; this prevents the core worker from depending on a vendor-specific public API. `mock` is development-only and is visibly recorded in provenance.

For production rendering, configure `AVATAR_UNREAL_COMMAND` and `AVATAR_UNREAL_PROJECT`. The custom Unreal MRQ executor at `/Game/Python/avatar_mrq_executor.py` must load the avatar asset bundle, retarget the JSON animation, and write an alpha-capable ProRes 4444 MOV or alpha WebM. Enable MRQ and Alpha Output in the Unreal project. The task inserts the generated media as a silent top B-Roll overlay, so original dialogue remains intact.

An executor template is included at `backend/unreal/avatar_mrq_executor.py`; copy it to the Unreal project's `Content/Python` directory and implement the project-owned `AvatarBridge` Control Rig/Blueprint bridge.

Require asset licence confirmation and subject consent. Output is marked `digital_avatar` in Timeline settings and job provenance; surface that disclosure in published exports.
