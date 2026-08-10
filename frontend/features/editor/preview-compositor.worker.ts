/// <reference lib="webworker" />

export {};

type Track = "main_video" | "b_roll" | "audio_overlay" | "multicam_video";
interface PreviewClip {
  id: string;
  track: Track;
  sourceAssetId: string;
  sourceStartMs: number;
  sourceEndMs: number;
  timelineStartMs: number;
  zIndex: number;
  enabled?: boolean;
  opacity?: number;
  /** Optional alpha-video source produced by the matting pipeline. */
  maskAssetId?: string;
  /** Blend amount of the uploaded 3D LUT, from 0 to 1. */
  lutIntensity?: number;
}
interface PreviewSubtitle {
  id: string;
  startMs: number;
  endMs: number;
  text: string;
  /** Normalized canvas coordinates; defaults keep social captions above the bottom safe area. */
  x?: number;
  y?: number;
  fontSize?: number;
  color?: string;
  strokeColor?: string;
}
interface EncodedChunkPayload { assetId: string; type: EncodedVideoChunkType; timestamp: number; duration?: number; data: ArrayBuffer; }
interface SourceConfig { assetId: string; decoderConfig: VideoDecoderConfig; }
interface WorkerSourceConfig extends SourceConfig { proxyUrl: string; }
interface LutPayload { dimension: number; data: ArrayBuffer; }
interface PreviewTransform { x: number; y: number; scale: number; rotation_degrees: number; z: number; }

/** Owns VideoFrames and closes frames immediately on eviction. Map insertion order is LRU order. */
class VideoFrameLRU {
  private readonly frames = new Map<number, VideoFrame>();
  constructor(private limit: number) {}

  setLimit(limit: number): void { this.limit = limit; this.evict(); }
  clear(): void { this.frames.forEach((frame) => frame.close()); this.frames.clear(); }
  put(frame: VideoFrame): void {
    const existing = this.frames.get(frame.timestamp);
    existing?.close();
    this.frames.delete(frame.timestamp);
    this.frames.set(frame.timestamp, frame);
    this.evict();
  }
  nearest(timestampUs: number, toleranceUs = 50_000): VideoFrame | undefined {
    let winner: [number, VideoFrame] | undefined;
    let distance = Number.POSITIVE_INFINITY;
    for (const entry of this.frames) {
      const candidateDistance = Math.abs(entry[0] - timestampUs);
      if (candidateDistance < distance) { winner = entry; distance = candidateDistance; }
    }
    if (!winner || distance > toleranceUs) return undefined;
    // Read counts as use: promote entry to the most-recent end of the map.
    this.frames.delete(winner[0]); this.frames.set(winner[0], winner[1]);
    return winner[1];
  }
  private evict(): void {
    while (this.frames.size > this.limit) {
      const key = this.frames.keys().next().value as number | undefined;
      if (key === undefined) return;
      this.frames.get(key)?.close(); this.frames.delete(key);
    }
  }
}

let canvas: OffscreenCanvas | null = null;
let gl: WebGL2RenderingContext | null = null;
let program: WebGLProgram | null = null;
let positionBuffer: WebGLBuffer | null = null;
let videoTexture: WebGLTexture | null = null;
let maskTexture: WebGLTexture | null = null;
let lutTexture: WebGLTexture | null = null;
let subtitleTexture: WebGLTexture | null = null;
let subtitleCanvas: OffscreenCanvas | null = null;
let subtitleContext: OffscreenCanvasRenderingContext2D | null = null;
let clips: PreviewClip[] = [];
let subtitles: PreviewSubtitle[] = [];
let antiAliasing = true;
let maxFrames = 60;
let buffered = false;
let lastDrawTimeMs = 0;
const caches = new Map<string, VideoFrameLRU>();
const decoders = new Map<string, VideoDecoder>();
const sources = new Map<string, WorkerSourceConfig>();
const pendingLoads = new Map<string, { demuxerModuleUrl: string; startUs: number; endUs: number }>();
const loadingAssets = new Set<string>();
const liveTransforms = new Map<string, PreviewTransform>();

const vertexShaderSource = `#version 300 es
in vec2 a_position;
uniform vec4 u_transform;
out vec2 v_uv;
void main() {
  v_uv = a_position * .5 + .5;
  float angle = radians(u_transform.w); float zoom = max(.01, u_transform.z);
  mat2 rotation = mat2(cos(angle), -sin(angle), sin(angle), cos(angle));
  vec2 translated = vec2((u_transform.x - .5) * 2., (.5 - u_transform.y) * 2.);
  gl_Position = vec4(rotation * (a_position * zoom) + translated, 0., 1.);
}`;
const fragmentShaderSource = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_video;
uniform sampler2D u_mask;
uniform sampler3D u_lut;
uniform bool u_hasMask;
uniform bool u_hasLut;
uniform float u_opacity;
uniform float u_lutIntensity;
out vec4 outColor;
void main() {
  vec4 source = texture(u_video, v_uv);
  float alpha = u_hasMask ? texture(u_mask, v_uv).r : 1.0;
  vec3 graded = u_hasLut ? texture(u_lut, clamp(source.rgb, 0.0, 1.0)).rgb : source.rgb;
  source.rgb = mix(source.rgb, graded, clamp(u_lutIntensity, 0.0, 1.0));
  outColor = vec4(source.rgb, source.a * alpha * u_opacity);
}`;

function compile(type: number, source: string): WebGLShader {
  const context = gl!; const shader = context.createShader(type)!;
  context.shaderSource(shader, source); context.compileShader(shader);
  if (!context.getShaderParameter(shader, context.COMPILE_STATUS)) throw new Error(context.getShaderInfoLog(shader) ?? "WebGL shader compile failed");
  return shader;
}

function initWebGL(target: OffscreenCanvas): void {
  gl = target.getContext("webgl2", { alpha: false, antialias: antiAliasing, premultipliedAlpha: false });
  if (!gl) throw new Error("WebGL2 is required for the WebCodecs preview compositor");
  const context = gl; program = context.createProgram()!;
  context.attachShader(program, compile(context.VERTEX_SHADER, vertexShaderSource));
  context.attachShader(program, compile(context.FRAGMENT_SHADER, fragmentShaderSource));
  context.linkProgram(program);
  if (!context.getProgramParameter(program, context.LINK_STATUS)) throw new Error(context.getProgramInfoLog(program) ?? "WebGL program link failed");
  positionBuffer = context.createBuffer(); context.bindBuffer(context.ARRAY_BUFFER, positionBuffer);
  context.bufferData(context.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), context.STATIC_DRAW);
  const texture = () => {
    const value = context.createTexture()!; context.bindTexture(context.TEXTURE_2D, value);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_MIN_FILTER, antiAliasing ? context.LINEAR : context.NEAREST);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_MAG_FILTER, antiAliasing ? context.LINEAR : context.NEAREST);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_WRAP_S, context.CLAMP_TO_EDGE);
    context.texParameteri(context.TEXTURE_2D, context.TEXTURE_WRAP_T, context.CLAMP_TO_EDGE);
    return value;
  };
  videoTexture = texture(); maskTexture = texture(); subtitleTexture = texture(); lutTexture = context.createTexture();
  context.bindTexture(context.TEXTURE_3D, lutTexture);
  context.texParameteri(context.TEXTURE_3D, context.TEXTURE_MIN_FILTER, context.LINEAR);
  context.texParameteri(context.TEXTURE_3D, context.TEXTURE_MAG_FILTER, context.LINEAR);
  context.texParameteri(context.TEXTURE_3D, context.TEXTURE_WRAP_S, context.CLAMP_TO_EDGE);
  context.texParameteri(context.TEXTURE_3D, context.TEXTURE_WRAP_T, context.CLAMP_TO_EDGE);
  context.texParameteri(context.TEXTURE_3D, context.TEXTURE_WRAP_R, context.CLAMP_TO_EDGE);
}

function ensureSubtitleCanvas(): OffscreenCanvasRenderingContext2D | null {
  if (!canvas) return null;
  if (!subtitleCanvas || subtitleCanvas.width !== canvas.width || subtitleCanvas.height !== canvas.height) {
    subtitleCanvas = new OffscreenCanvas(canvas.width, canvas.height);
    subtitleContext = subtitleCanvas.getContext("2d", { alpha: true });
  }
  return subtitleContext;
}

function setSamplingQuality(): void {
  if (!gl) return;
  const filter = antiAliasing ? gl.LINEAR : gl.NEAREST;
  for (const texture of [videoTexture, maskTexture]) {
    if (!texture) continue;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
  }
  if (lutTexture) {
    gl.bindTexture(gl.TEXTURE_3D, lutTexture);
    // LUT interpolation is color-critical; retain trilinear sampling even in emergency mode.
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }
}

function cacheFor(assetId: string): VideoFrameLRU {
  let cache = caches.get(assetId);
  if (!cache) { cache = new VideoFrameLRU(maxFrames); caches.set(assetId, cache); }
  return cache;
}
function closeFrames(assetId: string): void { caches.get(assetId)?.clear(); caches.delete(assetId); }

function uploadFrame(unit: number, texture: WebGLTexture, frame: VideoFrame): void {
  const context = gl!; context.activeTexture(context.TEXTURE0 + unit); context.bindTexture(context.TEXTURE_2D, texture);
  context.pixelStorei(context.UNPACK_FLIP_Y_WEBGL, true);
  context.texImage2D(context.TEXTURE_2D, 0, context.RGBA, context.RGBA, context.UNSIGNED_BYTE, frame);
}

function drawActiveSubtitles(timeMs: number): void {
  const context = gl; const activeProgram = program;
  if (!context || !activeProgram || !positionBuffer || !subtitleTexture) return;
  const active = subtitles.filter((subtitle) => timeMs >= subtitle.startMs && timeMs < subtitle.endMs);
  if (!active.length) return;
  const textContext = ensureSubtitleCanvas();
  if (!textContext || !subtitleCanvas) return;
  textContext.clearRect(0, 0, subtitleCanvas.width, subtitleCanvas.height);
  for (const subtitle of active) {
    const size = subtitle.fontSize ?? Math.max(30, Math.round(subtitleCanvas.height * .06));
    const x = (subtitle.x ?? .5) * subtitleCanvas.width;
    const y = (subtitle.y ?? .82) * subtitleCanvas.height;
    textContext.save();
    textContext.font = `900 ${size}px system-ui, "Noto Sans TC", sans-serif`;
    textContext.textAlign = "center";
    textContext.textBaseline = "middle";
    textContext.lineJoin = "round";
    textContext.lineWidth = Math.max(3, Math.round(size * .13));
    textContext.strokeStyle = subtitle.strokeColor ?? "#111111";
    textContext.fillStyle = subtitle.color ?? "#ffffff";
    textContext.strokeText(subtitle.text, x, y);
    textContext.fillText(subtitle.text, x, y);
    textContext.restore();
  }
  context.activeTexture(context.TEXTURE0); context.bindTexture(context.TEXTURE_2D, subtitleTexture);
  context.pixelStorei(context.UNPACK_FLIP_Y_WEBGL, true);
  context.texImage2D(context.TEXTURE_2D, 0, context.RGBA, context.RGBA, context.UNSIGNED_BYTE, subtitleCanvas);
  context.uniform1i(context.getUniformLocation(activeProgram, "u_video"), 0);
  context.uniform1i(context.getUniformLocation(activeProgram, "u_mask"), 1);
  context.uniform1i(context.getUniformLocation(activeProgram, "u_lut"), 2);
  context.uniform1i(context.getUniformLocation(activeProgram, "u_hasMask"), 0);
  context.uniform1i(context.getUniformLocation(activeProgram, "u_hasLut"), 0);
  context.uniform1f(context.getUniformLocation(activeProgram, "u_opacity"), 1);
  context.uniform1f(context.getUniformLocation(activeProgram, "u_lutIntensity"), 0);
  context.uniform4f(context.getUniformLocation(activeProgram, "u_transform"), .5, .5, 1, 0);
  context.drawArrays(context.TRIANGLES, 0, 6);
}

function draw(timeMs: number): void {
  const context = gl; const activeProgram = program;
  if (!canvas || !context || !activeProgram || !positionBuffer || !videoTexture || !maskTexture || !lutTexture) return;
  lastDrawTimeMs = timeMs;
  const active = clips.filter((clip) => {
    const duration = clip.sourceEndMs - clip.sourceStartMs;
    return clip.enabled !== false && clip.track !== "audio_overlay" && timeMs >= clip.timelineStartMs && timeMs < clip.timelineStartMs + duration;
  }).sort((a, b) => a.zIndex - b.zIndex);
  const layers: Array<{ clip: PreviewClip; frame: VideoFrame; mask?: VideoFrame }> = [];
  const missing: string[] = [];
  for (const clip of active) {
    const timeUs = (clip.sourceStartMs + timeMs - clip.timelineStartMs) * 1_000;
    const frame = cacheFor(clip.sourceAssetId).nearest(timeUs);
    if (!frame) { missing.push(clip.sourceAssetId); continue; }
    const mask = clip.maskAssetId ? cacheFor(clip.maskAssetId).nearest(timeUs) : undefined;
    layers.push({ clip, frame, mask });
  }
  // Do not clear when decoding catches up: the previous good frame stays visible.
  if (missing.length > 0 || layers.length === 0) {
    if (!buffered) { buffered = true; workerScope.postMessage({ type: "buffering", active: true, assetIds: [...new Set(missing)] }); }
    return;
  }
  if (buffered) { buffered = false; workerScope.postMessage({ type: "buffering", active: false, assetIds: [] }); }
  context.viewport(0, 0, canvas.width, canvas.height); context.clearColor(0, 0, 0, 1); context.clear(context.COLOR_BUFFER_BIT);
  context.useProgram(activeProgram); context.bindBuffer(context.ARRAY_BUFFER, positionBuffer);
  const position = context.getAttribLocation(activeProgram, "a_position"); context.enableVertexAttribArray(position); context.vertexAttribPointer(position, 2, context.FLOAT, false, 0, 0);
  context.enable(context.BLEND); context.blendFunc(context.SRC_ALPHA, context.ONE_MINUS_SRC_ALPHA);
  for (const { clip, frame, mask } of layers) {
    uploadFrame(0, videoTexture, frame); context.uniform1i(context.getUniformLocation(activeProgram, "u_video"), 0);
    if (mask) uploadFrame(1, maskTexture, mask);
    context.uniform1i(context.getUniformLocation(activeProgram, "u_mask"), 1);
    context.activeTexture(context.TEXTURE2); context.bindTexture(context.TEXTURE_3D, lutTexture);
    context.uniform1i(context.getUniformLocation(activeProgram, "u_lut"), 2);
    context.uniform1i(context.getUniformLocation(activeProgram, "u_hasMask"), mask ? 1 : 0);
    context.uniform1i(context.getUniformLocation(activeProgram, "u_hasLut"), clip.lutIntensity ? 1 : 0);
    context.uniform1f(context.getUniformLocation(activeProgram, "u_opacity"), clip.opacity ?? 1);
    context.uniform1f(context.getUniformLocation(activeProgram, "u_lutIntensity"), clip.lutIntensity ?? 0);
    const transform = liveTransforms.get(clip.id) ?? { x: .5, y: .5, scale: 1, rotation_degrees: 0, z: 0 };
    context.uniform4f(context.getUniformLocation(activeProgram, "u_transform"), transform.x, transform.y, transform.scale * (1 + transform.z * .2), transform.rotation_degrees);
    context.drawArrays(context.TRIANGLES, 0, 6);
  }
  drawActiveSubtitles(timeMs);
}

function uploadLut(payload: LutPayload): void {
  if (!gl || !lutTexture) return;
  const data = new Uint8Array(payload.data); const expected = payload.dimension ** 3 * 4;
  if (data.byteLength !== expected) throw new Error(`Invalid LUT byte length: expected ${expected}, got ${data.byteLength}`);
  gl.bindTexture(gl.TEXTURE_3D, lutTexture);
  gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, payload.dimension, payload.dimension, payload.dimension, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
}

/** Clones a cached decoded frame for Slip's split monitor without disturbing the program output canvas. */
function postGhostFrame(requestId: string, assetId: string, timeMs: number): void {
  const frame = cacheFor(assetId).nearest(Math.max(0, timeMs) * 1_000, 120_000);
  if (!frame) { workerScope.postMessage({ type: "ghost-frame-missing", requestId }); return; }
  createImageBitmap(frame).then((bitmap) => workerScope.postMessage({ type: "ghost-frame", requestId, bitmap }, [bitmap]))
    .catch(() => workerScope.postMessage({ type: "ghost-frame-missing", requestId }));
}

async function loadProxySegment(assetId: string, demuxerModuleUrl: string, startUs: number, endUs: number): Promise<void> {
  const source = sources.get(assetId); const decoder = decoders.get(assetId); if (!source || !decoder) return;
  const module = await import(/* webpackIgnore: true */ demuxerModuleUrl) as { demuxProxy: (options: { proxyUrl: string; startUs: number; endUs: number }) => AsyncIterable<EncodedChunkPayload>; };
  for await (const chunk of module.demuxProxy({ proxyUrl: source.proxyUrl, startUs, endUs })) {
    decoder.decode(new EncodedVideoChunk(chunk));
    if (decoder.decodeQueueSize > 24) await decoder.flush();
  }
  await decoder.flush();
}

/** One source has one VideoDecoder. Serialize and coalesce nearby seeks to keep decode order valid. */
async function queueProxySegment(assetId: string, demuxerModuleUrl: string, startUs: number, endUs: number): Promise<void> {
  const previous = pendingLoads.get(assetId);
  pendingLoads.set(assetId, previous
    ? { demuxerModuleUrl, startUs: Math.min(startUs, previous.startUs), endUs: Math.max(endUs, previous.endUs) }
    : { demuxerModuleUrl, startUs, endUs });
  if (loadingAssets.has(assetId)) return;
  loadingAssets.add(assetId);
  try {
    while (pendingLoads.has(assetId)) {
      const next = pendingLoads.get(assetId)!;
      pendingLoads.delete(assetId);
      await loadProxySegment(assetId, next.demuxerModuleUrl, next.startUs, next.endUs);
      draw(lastDrawTimeMs);
      workerScope.postMessage({ type: "decoded", assetId });
    }
  } finally {
    loadingAssets.delete(assetId);
  }
}

const workerScope = self as unknown as { onmessage: ((event: MessageEvent<any>) => void) | null; postMessage: (payload: unknown, transfer?: Transferable[]) => void; close: () => void; };
workerScope.onmessage = (event: MessageEvent<
  | { type: "init"; canvas: OffscreenCanvas; width: number; height: number; antiAliasing: boolean; maxFrames: number }
  | { type: "quality"; width: number; height: number; antiAliasing: boolean; maxFrames: number }
  | { type: "timeline"; clips: PreviewClip[]; subtitles?: PreviewSubtitle[] }
  | { type: "register-source"; source: WorkerSourceConfig }
  | { type: "decode"; chunk: EncodedChunkPayload }
  | { type: "load-proxy"; assetId: string; demuxerModuleUrl: string; startUs: number; endUs: number }
  | { type: "set-lut"; lut: LutPayload }
  | { type: "preview-transform"; clipId: string; value: PreviewTransform | null }
  | { type: "request-ghost-frame"; requestId: string; assetId: string; timeMs: number }
  | { type: "seek"; timeMs: number }
  | { type: "dispose" }
>) => {
  const message = event.data;
  try {
    if (message.type === "init") {
      canvas = message.canvas; canvas.width = message.width; canvas.height = message.height; antiAliasing = message.antiAliasing; maxFrames = message.maxFrames; initWebGL(canvas);
    } else if (message.type === "quality" && canvas) {
      canvas.width = message.width; canvas.height = message.height; antiAliasing = message.antiAliasing; maxFrames = message.maxFrames; setSamplingQuality(); caches.forEach((cache) => cache.setLimit(maxFrames));
    } else if (message.type === "timeline") { clips = message.clips; subtitles = message.subtitles ?? []; }
    else if (message.type === "register-source") {
      decoders.get(message.source.assetId)?.close(); closeFrames(message.source.assetId);
      const decoder = new VideoDecoder({ output: (frame) => cacheFor(message.source.assetId).put(frame), error: (error) => workerScope.postMessage({ type: "error", message: error.message }) });
      decoder.configure(message.source.decoderConfig); decoders.set(message.source.assetId, decoder); sources.set(message.source.assetId, message.source);
    } else if (message.type === "decode") decoders.get(message.chunk.assetId)?.decode(new EncodedVideoChunk(message.chunk));
    else if (message.type === "load-proxy") void queueProxySegment(message.assetId, message.demuxerModuleUrl, message.startUs, message.endUs).catch((error: unknown) => workerScope.postMessage({ type: "error", message: error instanceof Error ? error.message : "Worker demux failed" }));
    else if (message.type === "set-lut") uploadLut(message.lut);
    else if (message.type === "preview-transform") { if (message.value) liveTransforms.set(message.clipId, message.value); else liveTransforms.delete(message.clipId); draw(lastDrawTimeMs); }
    else if (message.type === "request-ghost-frame") postGhostFrame(message.requestId, message.assetId, message.timeMs);
    else if (message.type === "seek") draw(message.timeMs);
    else if (message.type === "dispose") { decoders.forEach((decoder) => decoder.close()); decoders.clear(); sources.clear(); pendingLoads.clear(); loadingAssets.clear(); liveTransforms.clear(); caches.forEach((cache) => cache.clear()); caches.clear(); workerScope.close(); }
  } catch (error) { workerScope.postMessage({ type: "error", message: error instanceof Error ? error.message : "Preview worker failed" }); }
};
