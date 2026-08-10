"use client";

import { useEffect, useRef } from "react";

import { useFaceMesh } from "@/features/editor/use-face-mesh";

export interface BeautyPreviewSettings { enabled: boolean; skin_smoothing: number; brightness: number; contrast: number; }

const vertex = `attribute vec2 aPosition; varying vec2 vUv; void main() { vUv = (aPosition + 1.0) * .5; gl_Position = vec4(aPosition, 0.0, 1.0); }`;
const fragment = `precision highp float;
varying vec2 vUv; uniform sampler2D uVideo; uniform vec2 uTexel; uniform vec2 uFaceCenter; uniform vec2 uFaceSize;
uniform float uSmoothing; uniform float uBrightness; uniform float uContrast; uniform float uEnabled;
float faceMask(vec2 uv) { vec2 p = (uv - uFaceCenter) / max(uFaceSize, vec2(.001)); p.x *= .86; return 1.0 - smoothstep(.74, 1.08, dot(p, p)); }
void main() {
  vec3 source = texture2D(uVideo, vUv).rgb; float mask = faceMask(vUv) * uEnabled;
  vec3 blur = source * .28;
  blur += texture2D(uVideo, vUv + vec2(uTexel.x, 0.)).rgb * .12; blur += texture2D(uVideo, vUv - vec2(uTexel.x, 0.)).rgb * .12;
  blur += texture2D(uVideo, vUv + vec2(0., uTexel.y)).rgb * .12; blur += texture2D(uVideo, vUv - vec2(0., uTexel.y)).rgb * .12;
  blur += texture2D(uVideo, vUv + uTexel).rgb * .06; blur += texture2D(uVideo, vUv - uTexel).rgb * .06;
  vec3 color = mix(source, blur, mask * uSmoothing * .62);
  color += vec3(uBrightness * .11) * mask;
  color = (color - .5) * (1.0 + uContrast * .45) + .5;
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}`;

function compile(gl: WebGLRenderingContext, kind: number, source: string): WebGLShader {
  const shader = gl.createShader(kind); if (!shader) throw new Error("WebGL shader allocation failed");
  gl.shaderSource(shader, source); gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? "Beauty shader compilation failed");
  return shader;
}

/** Overlay this canvas above the proxy video. Face Mesh scopes skin smoothing/brightening to the detected face. */
export function BeautyWebGLPreview({ video, settings, className }: { video: HTMLVideoElement | null; settings: BeautyPreviewSettings; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null); const face = useFaceMesh(video, settings.enabled);
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas || !video) return;
    const gl = canvas.getContext("webgl", { alpha: false, premultipliedAlpha: false }); if (!gl) return;
    const program = gl.createProgram(); if (!program) return;
    try {
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertex)); gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragment)); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Beauty shader link failed");
    } catch { return; }
    const buffer = gl.createBuffer(); const texture = gl.createTexture(); if (!buffer || !texture) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "aPosition"); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    gl.bindTexture(gl.TEXTURE_2D, texture); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    let frame = 0;
    const render = () => {
      if (!video.videoWidth || !video.videoHeight) { frame = requestAnimationFrame(render); return; }
      if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) { canvas.width = video.videoWidth; canvas.height = video.videoHeight; gl.viewport(0, 0, canvas.width, canvas.height); }
      gl.useProgram(program); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      gl.uniform1i(gl.getUniformLocation(program, "uVideo"), 0); gl.uniform2f(gl.getUniformLocation(program, "uTexel"), 1 / canvas.width, 1 / canvas.height);
      gl.uniform2f(gl.getUniformLocation(program, "uFaceCenter"), face.center[0], face.center[1]); gl.uniform2f(gl.getUniformLocation(program, "uFaceSize"), face.size[0], face.size[1]);
      gl.uniform1f(gl.getUniformLocation(program, "uSmoothing"), settings.skin_smoothing / 100); gl.uniform1f(gl.getUniformLocation(program, "uBrightness"), settings.brightness / 100); gl.uniform1f(gl.getUniformLocation(program, "uContrast"), settings.contrast / 100); gl.uniform1f(gl.getUniformLocation(program, "uEnabled"), settings.enabled ? 1 : 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);
    return () => { cancelAnimationFrame(frame); gl.deleteBuffer(buffer); gl.deleteTexture(texture); gl.deleteProgram(program); };
  }, [video, face, settings]);
  return <canvas ref={canvasRef} aria-label="WebGL 即時美顏預覽" className={className ?? "pointer-events-none absolute inset-0 h-full w-full"} />;
}
