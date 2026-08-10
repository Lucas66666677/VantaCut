"use client";

import { useEffect, useMemo, useRef } from "react";

export interface TravelRoutePoint { label: string; longitude: number; latitude: number; }

function project(points: TravelRoutePoint[]): Array<[number, number]> {
  const longitudes = points.map((point) => point.longitude); const latitudes = points.map((point) => point.latitude);
  const minX = Math.min(...longitudes); const maxX = Math.max(...longitudes); const minY = Math.min(...latitudes); const maxY = Math.max(...latitudes);
  const spanX = Math.max(0.01, maxX - minX); const spanY = Math.max(0.01, maxY - minY);
  return points.map((point) => [-.72 + ((point.longitude - minX) / spanX) * 1.44, .60 - ((point.latitude - minY) / spanY) * 1.20]);
}

function bezier(start: [number, number], end: [number, number]): Array<[number, number]> {
  const control: [number, number] = [(start[0] + end[0]) / 2, Math.min(start[1], end[1]) + .22 + Math.abs(end[0] - start[0]) * .16];
  return Array.from({ length: 81 }, (_, index) => {
    const t = index / 80; const inverse = 1 - t;
    return [inverse * inverse * start[0] + 2 * inverse * t * control[0] + t * t * end[0], inverse * inverse * start[1] + 2 * inverse * t * control[1] + t * t * end[1]];
  });
}

function routeCurve(points: TravelRoutePoint[]): Array<[number, number]> {
  const positions = project(points); return positions.flatMap((point, index) => index === 0 ? [] : bezier(positions[index - 1], point));
}

const vertexShader = `attribute vec2 position; uniform float pointSize; void main(){gl_Position=vec4(position,0.,1.);gl_PointSize=pointSize;}`;
const fragmentShader = `precision mediump float; uniform vec4 colour; void main(){if(length(gl_PointCoord-.5)>.5) discard;gl_FragColor=colour;}`;

function compile(gl: WebGLRenderingContext, kind: number, source: string): WebGLShader {
  const shader = gl.createShader(kind); if (!shader) throw new Error("WebGL shader unavailable"); gl.shaderSource(shader, source); gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? "WebGL shader compilation failed"); return shader;
}

/** A dependency-free WebGL route preview; exported media is rendered server-side by the identical Bezier plan. */
export function TravelRouteWebGLPreview({ route, vehicle = "plane" }: { route: TravelRoutePoint[]; vehicle?: "plane" | "car" }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null); const vehicleRef = useRef<HTMLSpanElement | null>(null);
  const curve = useMemo(() => route.length > 1 ? routeCurve(route) : [], [route]);
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas || curve.length === 0) return;
    const gl = canvas.getContext("webgl", { alpha: false, antialias: true }); if (!gl) return;
    const program = gl.createProgram(); if (!program) return;
    try { gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexShader)); gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentShader)); gl.linkProgram(program); } catch { return; }
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    const position = gl.getAttribLocation(program, "position"); const pointSize = gl.getUniformLocation(program, "pointSize"); const colour = gl.getUniformLocation(program, "colour"); const buffer = gl.createBuffer();
    if (!buffer || position < 0 || !pointSize || !colour) return;
    let animationFrame = 0; const started = performance.now();
    const render = (now: number) => {
      const progress = ((now - started) % 3500) / 3500; const shown = Math.max(2, Math.floor(curve.length * progress));
      const dots = curve.slice(0, shown).filter((_, index) => Math.floor(index / 3) % 2 === 0).flat();
      gl.viewport(0, 0, canvas.width, canvas.height); gl.clearColor(.027, .067, .126, 1); gl.clear(gl.COLOR_BUFFER_BIT); gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(dots), gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0); gl.uniform1f(pointSize, 5); gl.uniform4f(colour, .38, .65, 1, 1); gl.drawArrays(gl.POINTS, 0, dots.length / 2);
      const markers = project(route).flat(); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(markers), gl.DYNAMIC_DRAW); gl.uniform1f(pointSize, 12); gl.uniform4f(colour, .88, .95, 1, 1); gl.drawArrays(gl.POINTS, 0, markers.length / 2);
      const active = curve[shown - 1];
      if (vehicleRef.current) { vehicleRef.current.style.left = `${(active[0] + 1) * 50}%`; vehicleRef.current.style.top = `${(1 - active[1]) * 50}%`; }
      animationFrame = requestAnimationFrame(render);
    };
    animationFrame = requestAnimationFrame(render); return () => cancelAnimationFrame(animationFrame);
  }, [curve, route]);
  if (route.length < 2) return <div className="grid h-44 place-items-center rounded-lg border border-dashed border-sky-300/20 bg-slate-950 text-xs text-zinc-500">完成地名定位後顯示 WebGL 路線預覽</div>;
  const markers = project(route);
  return <div className="relative overflow-hidden rounded-lg border border-sky-300/20 bg-slate-950"><canvas ref={canvasRef} width={520} height={250} className="h-44 w-full" />{route.map((point, index) => <span key={`${point.label}-${index}`} className="absolute text-[10px] text-sky-100" style={{ left: `${((markers[index][0] + 1) * 50) + 2}%`, top: `${((1 - markers[index][1]) * 50) + 3}%` }}>{point.label}</span>)}<span ref={vehicleRef} className="pointer-events-none absolute left-1/2 top-1/2 text-2xl drop-shadow" style={{ transform: "translate(-50%, -50%)" }}>{vehicle === "plane" ? "✈️" : "🚗"}</span></div>;
}
