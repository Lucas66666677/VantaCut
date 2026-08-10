"use client";

import { useEffect, useRef } from "react";

export type CaptionEmotion = "neutral" | "emphasis" | "surprise" | "anger" | "joy" | "sadness";
export type CaptionPreset = "none" | "spring" | "pop" | "shake" | "explode" | "float";
export type CaptionVisualStyle = "viral_yellow" | "karaoke_pop" | "clean_white";

export interface KineticCaptionWord {
  word: string;
  start: number;
  end: number;
  emotion?: CaptionEmotion;
  emotion_intensity?: number;
  animation_preset?: CaptionPreset;
  highlight_kind?: "none" | "verb" | "number";
}

export interface KineticCaptionCue {
  id: string;
  start_time: number;
  end_time: number;
  text: string;
  words: KineticCaptionWord[];
}

interface KineticCaptionCanvasProps {
  cues: KineticCaptionCue[];
  currentTimeMs: number;
  width: number;
  height: number;
  stylePreset?: CaptionVisualStyle;
  className?: string;
}

type Runtime = {
  gl: WebGL2RenderingContext;
  textCanvas: HTMLCanvasElement;
  textTexture: WebGLTexture;
  quadProgram: WebGLProgram;
  particleProgram: WebGLProgram;
  quadBuffer: WebGLBuffer;
  particleBuffer: WebGLBuffer;
};

const QUAD_VERTEX = `#version 300 es
in vec2 aPosition; in vec2 aUv; out vec2 vUv;
void main() { gl_Position = vec4(aPosition, 0.0, 1.0); vUv = aUv; }`;
const QUAD_FRAGMENT = `#version 300 es
precision highp float; uniform sampler2D uTexture; in vec2 vUv; out vec4 outColor;
void main() { outColor = texture(uTexture, vUv); }`;
const PARTICLE_VERTEX = `#version 300 es
in vec2 aPosition; in vec4 aColor; in float aSize; uniform vec2 uResolution; out vec4 vColor;
void main() { vec2 p = aPosition / uResolution * 2.0 - 1.0; gl_Position = vec4(p.x, -p.y, 0.0, 1.0); gl_PointSize = aSize; vColor = aColor; }`;
const PARTICLE_FRAGMENT = `#version 300 es
precision highp float; in vec4 vColor; out vec4 outColor;
void main() { if (length(gl_PointCoord - .5) > .5) discard; outColor = vColor; }`;

function shader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const value = gl.createShader(type);
  if (!value) throw new Error("Unable to allocate WebGL shader");
  gl.shaderSource(value, source);
  gl.compileShader(value);
  if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(value) ?? "WebGL shader error");
  return value;
}

function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const value = gl.createProgram();
  if (!value) throw new Error("Unable to allocate WebGL program");
  gl.attachShader(value, shader(gl, gl.VERTEX_SHADER, vertex));
  gl.attachShader(value, shader(gl, gl.FRAGMENT_SHADER, fragment));
  gl.linkProgram(value);
  if (!gl.getProgramParameter(value, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(value) ?? "WebGL link error");
  return value;
}

function initialise(canvas: HTMLCanvasElement): Runtime | null {
  const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: false });
  if (!gl) return null;
  const textTexture = gl.createTexture();
  const quadBuffer = gl.createBuffer();
  const particleBuffer = gl.createBuffer();
  if (!textTexture || !quadBuffer || !particleBuffer) return null;
  gl.bindTexture(gl.TEXTURE_2D, textTexture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1, -1, 0, 1, 1, -1, 1, 1, -1, 1, 0, 0,
    -1, 1, 0, 0, 1, -1, 1, 1, 1, 1, 1, 0,
  ]), gl.STATIC_DRAW);
  const textCanvas = document.createElement("canvas");
  return {
    gl, textCanvas, textTexture, quadBuffer, particleBuffer,
    quadProgram: program(gl, QUAD_VERTEX, QUAD_FRAGMENT),
    particleProgram: program(gl, PARTICLE_VERTEX, PARTICLE_FRAGMENT),
  };
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));
// Starts compact, overshoots once, then settles. It runs only during the first ~1/3 of a word.
const spring = (progress: number) => 1 - Math.exp(-6 * progress) * Math.cos(progress * 15);

type WordState = "past" | "active" | "future";

function getWordState(word: KineticCaptionWord, now: number): WordState {
  if (now < word.start) return "future";
  if (now > word.end) return "past";
  return "active";
}

function drawWord(
  context: CanvasRenderingContext2D,
  word: KineticCaptionWord,
  x: number,
  y: number,
  now: number,
  particles: number[],
  stylePreset: CaptionVisualStyle,
  state: WordState,
) {
  const isActive = state === "active";
  const progress = clamp((now - word.start) / Math.max(.08, word.end - word.start));
  let scale = isActive ? .92 + .22 * spring(clamp(progress * 3.1)) : state === "past" ? .9 : .82;
  let offsetY = 0;
  let offsetX = 0;
  let fill = word.highlight_kind === "number" ? "#b8ff3d" : word.highlight_kind === "verb" ? "#ffe65b" : stylePreset === "viral_yellow" ? "#ffd84a" : "#fff";
  const strokeWidth = isActive ? 10 : 7;
  const preset = word.animation_preset ?? "none";
  if (isActive && preset === "pop") { scale = 1.3 - .3 * clamp(progress * 4); offsetY = (1 - clamp(progress * 4)) * 70; }
  if (isActive && preset === "shake") { offsetX = Math.sin(progress * 46) * (1 - progress) * 11; fill = "#ff7368"; }
  if (isActive && preset === "float") { offsetY = (1 - clamp(progress * 3)) * 22; fill = "#c4e0ff"; }
  if (isActive && preset === "explode") {
    const alpha = 1 - progress;
    for (let index = 0; index < 24; index += 1) {
      const angle = index * Math.PI * 2 / 24 + word.word.length * .31;
      const distance = 16 + 180 * progress * progress;
      particles.push(x + Math.cos(angle) * distance, y + Math.sin(angle) * distance, 1, .78, .18, alpha, 3 + (1 - progress) * 8);
    }
    if (progress > .14) return;
  }
  context.save();
  context.translate(x + offsetX, y - offsetY);
  context.scale(scale, scale);
  context.font = "800 64px 'Noto Sans TC', Arial, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.lineJoin = "round";
  context.globalAlpha = isActive ? 1 : state === "past" ? .68 : .38;
  context.shadowColor = "rgba(0, 0, 0, .92)";
  context.shadowBlur = isActive ? 16 : 10;
  context.shadowOffsetY = isActive ? 6 : 4;
  context.strokeStyle = "rgba(10, 10, 18, .92)";
  context.lineWidth = strokeWidth;
  context.strokeText(word.word, 0, 0);
  context.fillStyle = fill;
  context.fillText(word.word, 0, 0);
  context.restore();
}

function render(runtime: Runtime, cues: KineticCaptionCue[], now: number, width: number, height: number, stylePreset: CaptionVisualStyle) {
  const { gl, textCanvas } = runtime;
  textCanvas.width = width;
  textCanvas.height = height;
  const context = textCanvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, width, height);
  const particles: number[] = [];
  for (const cue of cues) {
    if (now < cue.start_time || now > cue.end_time) continue;
    const allWords = cue.words.length ? cue.words : [{ word: cue.text, start: cue.start_time, end: cue.end_time }];
    context.font = "800 64px 'Noto Sans TC', Arial, sans-serif";
    const total = allWords.reduce((sum, word) => sum + context.measureText(word.word).width + 15, 0);
    let cursor = (width - total) / 2;
    for (const word of allWords) {
      const advance = context.measureText(word.word).width + 15;
      drawWord(context, word, cursor + advance / 2, height * .75, now, particles, stylePreset, getWordState(word, now));
      cursor += advance;
    }
  }
  gl.viewport(0, 0, width, height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(runtime.quadProgram);
  gl.bindBuffer(gl.ARRAY_BUFFER, runtime.quadBuffer);
  const position = gl.getAttribLocation(runtime.quadProgram, "aPosition");
  const uv = gl.getAttribLocation(runtime.quadProgram, "aUv");
  gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 16, 0);
  gl.enableVertexAttribArray(uv); gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 16, 8);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, runtime.textTexture);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textCanvas);
  gl.uniform1i(gl.getUniformLocation(runtime.quadProgram, "uTexture"), 0);
  gl.drawArrays(gl.TRIANGLES, 0, 6);
  if (!particles.length) return;
  gl.useProgram(runtime.particleProgram);
  gl.bindBuffer(gl.ARRAY_BUFFER, runtime.particleBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(particles), gl.DYNAMIC_DRAW);
  const particlePosition = gl.getAttribLocation(runtime.particleProgram, "aPosition");
  const particleColor = gl.getAttribLocation(runtime.particleProgram, "aColor");
  const particleSize = gl.getAttribLocation(runtime.particleProgram, "aSize");
  gl.enableVertexAttribArray(particlePosition); gl.vertexAttribPointer(particlePosition, 2, gl.FLOAT, false, 28, 0);
  gl.enableVertexAttribArray(particleColor); gl.vertexAttribPointer(particleColor, 4, gl.FLOAT, false, 28, 8);
  gl.enableVertexAttribArray(particleSize); gl.vertexAttribPointer(particleSize, 1, gl.FLOAT, false, 28, 24);
  gl.uniform2f(gl.getUniformLocation(runtime.particleProgram, "uResolution"), width, height);
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.drawArrays(gl.POINTS, 0, particles.length / 7);
}

/** Place this absolutely above the WebCodecs proxy canvas; currentTimeMs comes from its playhead. */
export function KineticCaptionCanvas({ cues, currentTimeMs, width, height, stylePreset = "viral_yellow", className }: KineticCaptionCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const runtimeRef = useRef<Runtime | null>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = width; canvas.height = height;
    runtimeRef.current ??= initialise(canvas);
    if (runtimeRef.current) render(runtimeRef.current, cues, currentTimeMs / 1000, width, height, stylePreset);
  }, [cues, currentTimeMs, height, stylePreset, width]);
  return <canvas ref={canvasRef} width={width} height={height} className={className ?? "pointer-events-none absolute inset-0 h-full w-full"} />;
}
