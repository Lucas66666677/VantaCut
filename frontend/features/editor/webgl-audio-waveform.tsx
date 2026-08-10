"use client";

import { useEffect, useRef } from "react";

import { chooseWaveformLod, type WaveformLod } from "@/features/editor/use-audio-waveform";

interface WebglAudioWaveformProps {
  lods: WaveformLod[];
  viewportStartMs: number;
  viewportEndMs: number;
  pixelsPerSecond: number;
  className?: string;
  color?: [number, number, number, number];
}

const VERTEX = `#version 300 es
in vec2 a_corner;
in vec2 a_metrics;
in float a_timeMs;
uniform float u_startMs;
uniform float u_endMs;
uniform float u_barWidth;
out float v_rms;
void main() {
  float position = (a_timeMs - u_startMs) / max(1.0, u_endMs - u_startMs);
  float amplitude = mix(a_metrics.x, a_metrics.y, 0.72);
  float x = position * 2.0 - 1.0 + a_corner.x * u_barWidth;
  float y = a_corner.y * clamp(amplitude, 0.015, 1.0);
  v_rms = a_metrics.x;
  gl_Position = vec4(x, y, 0.0, 1.0);
}`;
const FRAGMENT = `#version 300 es
precision highp float;
in float v_rms;
uniform vec4 u_color;
out vec4 outColor;
void main() { outColor = vec4(u_color.rgb, u_color.a * mix(.45, 1., clamp(v_rms * 3., 0., 1.))); }`;

function shader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const value = gl.createShader(type)!; gl.shaderSource(value, source); gl.compileShader(value);
  if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(value) ?? "Waveform shader compile failed");
  return value;
}

/** Instanced WebGL bars: the CPU uploads only the visible LOD slice; no Canvas2D lineTo loop exists. */
export function WebglAudioWaveform({ lods, viewportStartMs, viewportEndMs, pixelsPerSecond, className = "", color = [0.13, 0.83, 0.91, 0.9] }: WebglAudioWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const inputRef = useRef({ lods, viewportStartMs, viewportEndMs, pixelsPerSecond, color });
  const requestDrawRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl2", { alpha: true, antialias: false });
    if (!gl) return;
    const program = gl.createProgram()!;
    gl.attachShader(program, shader(gl, gl.VERTEX_SHADER, VERTEX)); gl.attachShader(program, shader(gl, gl.FRAGMENT_SHADER, FRAGMENT)); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Waveform shader link failed");
    const quad = gl.createBuffer()!;
    const instance = gl.createBuffer()!;
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, quad); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-.5, -1, .5, -1, -.5, 1, .5, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, "a_corner"); gl.enableVertexAttribArray(corner); gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    const time = gl.getAttribLocation(program, "a_timeMs"); const metrics = gl.getAttribLocation(program, "a_metrics");
    gl.bindBuffer(gl.ARRAY_BUFFER, instance);
    gl.enableVertexAttribArray(time); gl.vertexAttribPointer(time, 1, gl.FLOAT, false, 12, 0); gl.vertexAttribDivisor(time, 1);
    gl.enableVertexAttribArray(metrics); gl.vertexAttribPointer(metrics, 2, gl.FLOAT, false, 12, 4); gl.vertexAttribDivisor(metrics, 1);
    let animationFrame = 0;
    const render = () => {
      const input = inputRef.current;
      const lod = chooseWaveformLod(input.lods, input.pixelsPerSecond);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      if (!lod) return;
      const first = Math.max(0, Math.floor(input.viewportStartMs / lod.resolutionMs));
      const last = Math.min(lod.values.length / 2, Math.ceil(input.viewportEndMs / lod.resolutionMs) + 1);
      const instances = Math.max(0, last - first);
      const data = new Float32Array(instances * 3);
      for (let index = 0; index < instances; index += 1) {
        const sourceIndex = first + index;
        data[index * 3] = sourceIndex * lod.resolutionMs + lod.resolutionMs * .5;
        data[index * 3 + 1] = lod.values[sourceIndex * 2] ?? 0;
        data[index * 3 + 2] = lod.values[sourceIndex * 2 + 1] ?? 0;
      }
      gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, instance); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
      gl.uniform1f(gl.getUniformLocation(program, "u_startMs"), input.viewportStartMs); gl.uniform1f(gl.getUniformLocation(program, "u_endMs"), input.viewportEndMs);
      gl.uniform1f(gl.getUniformLocation(program, "u_barWidth"), Math.min(.018, 1 / Math.max(20, instances)));
      gl.uniform4fv(gl.getUniformLocation(program, "u_color"), input.color);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, instances);
    };
    const requestDraw = () => { cancelAnimationFrame(animationFrame); animationFrame = requestAnimationFrame(render); };
    requestDrawRef.current = requestDraw;
    const resize = () => {
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * ratio)); canvas.height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      gl.viewport(0, 0, canvas.width, canvas.height);
      requestDraw();
    };
    const observer = new ResizeObserver(resize); observer.observe(canvas); resize();
    return () => { cancelAnimationFrame(animationFrame); requestDrawRef.current = () => undefined; observer.disconnect(); gl.deleteBuffer(quad); gl.deleteBuffer(instance); gl.deleteProgram(program); };
  }, []);

  useEffect(() => {
    inputRef.current = { lods, viewportStartMs, viewportEndMs, pixelsPerSecond, color };
    requestDrawRef.current();
  }, [color, lods, pixelsPerSecond, viewportEndMs, viewportStartMs]);

  return <canvas ref={canvasRef} aria-label="音訊波形" className={`block h-full w-full ${className}`} />;
}
