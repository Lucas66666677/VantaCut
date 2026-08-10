export type AnnotationOperation =
  | { kind: "stroke"; color: string; width: number; points: Array<{ x: number; y: number }> }
  | { kind: "circle"; color: string; width: number; start: { x: number; y: number }; end: { x: number; y: number } }
  | { kind: "text"; color: string; fontSize: number; x: number; y: number; text: string };

export interface FrameAnnotation {
  canvas_width: number;
  canvas_height: number;
  operations: AnnotationOperation[];
}

export interface ReviewComment {
  id: string;
  status: "open" | "resolved";
  time_seconds: number;
  timecode: string;
  frame_number: number;
  frame_rate: number;
  body: string;
  annotation: FrameAnnotation;
  author_name: string;
}
