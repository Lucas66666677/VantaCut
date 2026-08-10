export interface OscTransform { x: number; y: number; width: number; height: number; scale: number; rotation: number; }
export interface OscGuides { horizontal: boolean; vertical: boolean; angle: boolean; }
export type Mat3 = readonly [number, number, number, number, number, number, number, number, number];

export const OSC_VIEW_WIDTH = 1_000;
export const OSC_VIEW_HEIGHT = 562.5;
const CENTER_TOLERANCE = .015;
const ANGLE_TOLERANCE = 4;

export function multiplyMat3(left: Mat3, right: Mat3): Mat3 {
  const out = new Array<number>(9).fill(0);
  for (let row = 0; row < 3; row += 1) for (let column = 0; column < 3; column += 1) for (let index = 0; index < 3; index += 1) out[row * 3 + column] += left[row * 3 + index]! * right[index * 3 + column]!;
  return out as unknown as Mat3;
}
export const translateMat3 = (x: number, y: number): Mat3 => [1, 0, x, 0, 1, y, 0, 0, 1];
export const scaleMat3 = (x: number, y: number): Mat3 => [x, 0, 0, 0, y, 0, 0, 0, 1];
export const rotateMat3 = (degrees: number): Mat3 => { const radians = degrees * Math.PI / 180; const cos = Math.cos(radians); const sin = Math.sin(radians); return [cos, -sin, 0, sin, cos, 0, 0, 0, 1]; };
export function applyMat3(matrix: Mat3, point: { x: number; y: number }) { return { x: matrix[0] * point.x + matrix[1] * point.y + matrix[2], y: matrix[3] * point.x + matrix[4] * point.y + matrix[5] }; }

/** Local element coordinates → 16:9 video coordinates. This is the same affine matrix used by the SVG control box. */
export function transformMatrix(transform: OscTransform): Mat3 {
  const center = translateMat3(transform.x * OSC_VIEW_WIDTH, transform.y * OSC_VIEW_HEIGHT);
  return multiplyMat3(center, multiplyMat3(rotateMat3(transform.rotation), scaleMat3(transform.scale, transform.scale)));
}
export function screenToVideoPoint(clientX: number, clientY: number, rect: DOMRect) {
  return { x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)) };
}
export function videoToLocal(point: { x: number; y: number }, transform: OscTransform) {
  const dx = (point.x - transform.x) * OSC_VIEW_WIDTH;
  const dy = (point.y - transform.y) * OSC_VIEW_HEIGHT;
  const radians = -transform.rotation * Math.PI / 180; const cos = Math.cos(radians); const sin = Math.sin(radians);
  return { x: (dx * cos - dy * sin) / transform.scale, y: (dx * sin + dy * cos) / transform.scale };
}
export function snapTransform(transform: OscTransform): { transform: OscTransform; guides: OscGuides } {
  const nearestRightAngle = Math.round(transform.rotation / 90) * 90;
  const angle = Math.abs(transform.rotation - nearestRightAngle) <= ANGLE_TOLERANCE;
  const vertical = Math.abs(transform.x - .5) <= CENTER_TOLERANCE;
  const horizontal = Math.abs(transform.y - .5) <= CENTER_TOLERANCE;
  return { transform: { ...transform, x: vertical ? .5 : transform.x, y: horizontal ? .5 : transform.y, rotation: angle ? nearestRightAngle : transform.rotation }, guides: { horizontal, vertical, angle } };
}
