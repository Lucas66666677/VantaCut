export interface VirtualCameraKeyframe {
  time_seconds: number;
  position: [number, number, number];
  look_at: [number, number, number];
  fov_degrees: number;
}

export interface SpatialSceneManifest {
  scene_id: string;
  status: "completed" | "failed" | "processing";
  splat_url?: string;
  camera_poses_url?: string;
  frame_count?: number;
  registered_pose_count?: number;
  coordinate_system?: string;
}
