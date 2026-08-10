export interface InteractiveNode {
  id: string;
  title: string;
  media_asset_id: string;
  source_start: number;
  source_end: number;
  media_url: string;
}

export interface InteractiveEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  choice_text: string;
  choice_position: { x: number; y: number };
}

export interface InteractiveManifest {
  timeline_id: string;
  graph: { entry_node_id: string; edges: InteractiveEdge[] };
  nodes: InteractiveNode[];
}

export interface InteractiveAnalytics {
  timeline_id: string;
  sessions: number;
  nodes: { id: string; label: string; visits: number; average_dwell_seconds: number }[];
  links: { source: string; target: string; edge_id: string; label: string; value: number; choice_share_percent: number }[];
}
