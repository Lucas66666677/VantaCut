"""Copy into <UEProject>/Content/Python/avatar_mrq_executor.py.

Project-specific Blueprint/C++ function `AvatarBridge.apply_animation_document` must load the avatar,
retarget blendshapes/IK to its control rig, and configure a Level Sequence before MRQ executes.
"""
import json
import sys

import unreal


def _argument(name: str) -> str:
    prefix = f"-{name}="
    value = next((item[len(prefix):] for item in sys.argv if item.startswith(prefix)), None)
    if not value:
        raise RuntimeError(f"Missing {prefix} command-line argument")
    return value


@unreal.uclass()
class AvatarMRQExecutor(unreal.MoviePipelinePythonHostExecutor):
    @unreal.ufunction(override=True)
    def execute_delayed(self, pipeline_queue):
        animation_path, bundle_path, output_path = _argument("AvatarAnimation"), _argument("AvatarBundle"), _argument("AvatarOutput")
        with open(animation_path, encoding="utf-8") as handle:
            animation = json.load(handle)
        # Implement this project-owned bridge with a Control Rig/Blueprint. It must apply only
        # licensed assets and output a transparent pass (ProRes 4444 or alpha WebM).
        sequence = unreal.AvatarBridge.apply_animation_document(bundle_path, animation, output_path)
        job = pipeline_queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        job.sequence = unreal.SoftObjectPath(sequence)
        job.file_name_format = output_path
        self.on_executor_finished_impl()
