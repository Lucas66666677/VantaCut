export interface RetentionCurvePoint {
  time_seconds: number;
  expected_retention: number;
  risk_score: number;
}

export interface RetentionHotspot {
  id: string;
  start_time: number;
  end_time: number;
  predicted_drop: number;
  risk_score: number;
  reason: string;
  suggestion: string;
  feature_evidence: Record<string, number>;
}

export interface RetentionPrediction {
  timeline_id: string;
  model_name: string;
  prediction_mode: "checkpoint" | "heuristic_baseline";
  is_calibrated: boolean;
  window_seconds: number;
  curve: RetentionCurvePoint[];
  hotspots: RetentionHotspot[];
  summary: string;
}
