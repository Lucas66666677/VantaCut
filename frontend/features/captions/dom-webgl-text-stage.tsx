"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface HybridTextLayout { x: number; y: number; width: number; height: number; }

const VERTEX = `#version 300 es
in vec2 a_position; in vec2 a_uv; out vec2 v_uv;
void main(){ gl_Position=vec4(a_position,0.,1.); v_uv=a_uv; }`;
// RGB samples are offset by one third of a device texel: a small LCD-style
// sub-pixel reconstruction pass keeps thin strokes crisp after texture upload.
const FRAGMENT = `#version 300 es
precision highp float; uniform sampler2D u_texture; uniform vec2 u_texel; in vec2 v_uv; out vec4 out_color;
void main(){ vec4 center=texture(u_texture,v_uv); float r=texture(u_texture,v_uv-vec2(u_texel.x/3.,0.)).r; float g=center.g; float b=texture(u_texture,v_uv+vec2(u_texel.x/3.,0.)).b; out_color=vec4(r,g,b,center.a); }`;

function compile(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type); if (!shader) throw new Error("Text shader allocation failed"); gl.shaderSource(shader, source); gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? "Text shader compilation failed"); return shader;
}

function makeProgram(gl: WebGL2RenderingContext) {
  const program = gl.createProgram(); if (!program) throw new Error("Text program allocation failed"); gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX)); gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT)); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Text program link failed"); return program;
}

function deviceAligned(value: number, dpr: number) { return Math.round(value * dpr) / dpr; }

/** Rasterizes DOM using the browser's own typography stack; works with IME, ruby and mixed scripts. */
export async function rasterizeDomText(element: HTMLElement): Promise<ImageBitmap> {
  await document.fonts?.ready;
  const rect = element.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.ceil(rect.width * dpr)); const height = Math.max(1, Math.ceil(rect.height * dpr));
  const style = getComputedStyle(element);
  const serialisedStyle = ["font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height", "color", "text-align", "white-space", "text-shadow", "-webkit-text-stroke", "text-transform"].map((property) => `${property}:${style.getPropertyValue(property)};`).join("");
  const body = element.innerHTML || "&nbsp;";
  // CSS-space viewBox + physical bitmap dimensions yields Retina output without
  // scaling the foreignObject twice (which otherwise causes cropped glyphs).
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${rect.width} ${rect.height}"><foreignObject width="${rect.width}" height="${rect.height}"><div xmlns="http://www.w3.org/1999/xhtml" style="box-sizing:border-box;width:${rect.width}px;height:${rect.height}px;${serialisedStyle}">${body}</div></foreignObject></svg>`;
  return createImageBitmap(new Blob([svg], { type: "image/svg+xml" }));
}

function drawTexture(gl: WebGL2RenderingContext, program: WebGLProgram, texture: WebGLTexture, bitmap: ImageBitmap, canvas: HTMLCanvasElement, layout: DOMRect) {
  const canvasRect = canvas.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
  const x = deviceAligned(layout.left - canvasRect.left, dpr) * dpr; const y = deviceAligned(layout.top - canvasRect.top, dpr) * dpr;
  const width = deviceAligned(layout.width, dpr) * dpr; const height = deviceAligned(layout.height, dpr) * dpr;
  const cw = canvas.width; const ch = canvas.height;
  const left = x / cw * 2 - 1; const right = (x + width) / cw * 2 - 1; const top = 1 - y / ch * 2; const bottom = 1 - (y + height) / ch * 2;
  const data = new Float32Array([left, bottom, 0, 1, right, bottom, 1, 1, left, top, 0, 0, left, top, 0, 0, right, bottom, 1, 1, right, top, 1, 0]);
  const buffer = gl.createBuffer(); if (!buffer) return;
  gl.viewport(0, 0, cw, ch); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STREAM_DRAW);
  const position = gl.getAttribLocation(program, "a_position"); const uv = gl.getAttribLocation(program, "a_uv"); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 16, 0); gl.enableVertexAttribArray(uv); gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 16, 8);
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
  gl.uniform1i(gl.getUniformLocation(program, "u_texture"), 0); gl.uniform2f(gl.getUniformLocation(program, "u_texel"), 1 / bitmap.width, 1 / bitmap.height); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.drawArrays(gl.TRIANGLES, 0, 6); gl.deleteBuffer(buffer);
}

/** DOM while editing; a DPR-aware WebGL texture at rest. Both layers share one normalized layout model. */
export function DomWebglTextStage({ width, height, initialText = "雙擊輸入文字", onCommit }: { width: number; height: number; initialText?: string; onCommit?: (text: string, layout: HybridTextLayout) => void }) {
  const stageRef = useRef<HTMLDivElement>(null); const canvasRef = useRef<HTMLCanvasElement>(null); const textRef = useRef<HTMLDivElement>(null); const glRef = useRef<{ gl: WebGL2RenderingContext; texture: WebGLTexture; program: WebGLProgram } | null>(null);
  const [text, setText] = useState(initialText); const [editing, setEditing] = useState(false); const [position, setPosition] = useState({ x: .5, y: .72 });
  const rasterize = useCallback(async () => {
    const element = textRef.current; const canvas = canvasRef.current; if (!element || !canvas) return;
    const bitmap = await rasterizeDomText(element); const glState = glRef.current;
    if (glState) drawTexture(glState.gl, glState.program, glState.texture, bitmap, canvas, element.getBoundingClientRect());
    const stage = stageRef.current?.getBoundingClientRect(); if (stage) onCommit?.(element.innerText, { x: position.x, y: position.y, width: element.getBoundingClientRect().width / stage.width, height: element.getBoundingClientRect().height / stage.height });
    bitmap.close();
  }, [onCommit, position.x, position.y]);
  useLayoutEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return; const dpr = window.devicePixelRatio || 1; canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true }); if (!gl) return; const texture = gl.createTexture(); if (!texture) return; glRef.current = { gl, texture, program: makeProgram(gl) }; void rasterize();
    return () => { gl.deleteTexture(texture); gl.deleteProgram(glRef.current?.program ?? null); glRef.current = null; };
  }, [height, rasterize, width]);
  useEffect(() => { if (!editing) void rasterize(); }, [editing, rasterize, text]);
  return <div ref={stageRef} className="relative overflow-hidden rounded-lg bg-[linear-gradient(135deg,#334155,#020617)]" style={{ width, height }} onDoubleClick={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); setPosition({ x: Math.max(.06, Math.min(.94, (event.clientX - bounds.left) / bounds.width)), y: Math.max(.08, Math.min(.9, (event.clientY - bounds.top) / bounds.height)) }); setEditing(true); requestAnimationFrame(() => textRef.current?.focus()); }}>
    <canvas ref={canvasRef} className={`pointer-events-none absolute inset-0 ${editing ? "opacity-40" : "opacity-100"}`} />
    <div ref={textRef} contentEditable={editing} suppressContentEditableWarning spellCheck={false} onInput={(event) => setText(event.currentTarget.innerText)} onBlur={() => setEditing(false)} onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") { event.preventDefault(); document.execCommand("bold"); } if (event.key === "Escape") { event.currentTarget.blur(); } }} className={`absolute min-w-20 -translate-x-1/2 -translate-y-1/2 whitespace-pre-wrap rounded px-1 text-center text-[26px] font-black leading-tight text-yellow-300 [text-shadow:0_3px_0_#111,0_0_8px_#000] [-webkit-text-stroke:1px_rgba(0,0,0,.85)] outline-none ${editing ? "z-10 cursor-text ring-1 ring-cyan-200/70" : "pointer-events-none opacity-0"}`} style={{ left: `${position.x * 100}%`, top: `${position.y * 100}%` }}>{text}</div>
    {!editing && <span className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-black/45 px-2 py-1 text-[10px] text-zinc-300">雙擊預覽畫面即可編輯花字</span>}
  </div>;
}
