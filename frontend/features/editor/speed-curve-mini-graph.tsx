import { speedToGraphY, type ClipSpeedCurve } from "@/types/speed-curves";

export function SpeedCurveMiniGraph({ curve, width }: { curve: ClipSpeedCurve; width: number }) {
  const height = 22;
  const path = curve.points.map((point, index) => `${index ? "L" : "M"} ${(point.position * width).toFixed(1)} ${(speedToGraphY(point.speed) * height).toFixed(1)}`).join(" ");
  return <svg aria-label="速度曲線" viewBox={`0 0 ${width} ${height}`} width={width} height={height} className="pointer-events-none absolute -top-5 left-0 overflow-visible"><path d={path} fill="none" stroke="#fbbf24" strokeWidth="1.7" /><path d={`M 0 ${height} ${path.slice(1)} L ${width} ${height} Z`} fill="rgba(251,191,36,.14)" />{curve.points.map((point, index) => <circle key={index} cx={point.position * width} cy={speedToGraphY(point.speed) * height} r="2" fill="#fde68a" />)}</svg>;
}

