"use client";

import { useEffect, useRef } from "react";

import type { TransitionKind } from "@/types/transitions";

const VERTEX = `attribute vec2 p; varying vec2 uv; void main(){ uv=(p+1.)*.5; gl_Position=vec4(p,0.,1.); }`;
const COMMON = `precision highp float; varying vec2 uv; uniform sampler2D fromTex; uniform sampler2D toTex; uniform sampler2D depthTex; uniform float progress; uniform int kind;
float noise(vec2 p){ return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453); }
vec4 zoomBlur(sampler2D tex, vec2 p, float amount){ vec4 c=vec4(0.); for(int i=0;i<8;i++){float t=float(i)/7.; c+=texture2D(tex,mix(p,.5,t*amount));} return c/8.; }
void main(){ float p=clamp(progress,0.,1.); vec2 a=uv, b=uv; vec4 outColor;
if(kind==1){ float n=noise(vec2(floor(uv.y*70.),floor(p*40.))); a.x+=(n-.5)*.06*(1.-p); b.x+=(.5-n)*.06*p; outColor=mix(texture2D(fromTex,a),texture2D(toTex,b),p); }
else if(kind==2){ float d=.016*(1.-abs(2.*p-1.)); vec4 f=texture2D(fromTex,uv), t=texture2D(toTex,uv); vec4 m=mix(f,t,p); outColor=vec4(texture2D(fromTex,uv+vec2(d,0.)).r,m.g,texture2D(toTex,uv-vec2(d,0.)).b,m.a); }
else if(kind==3){ outColor=mix(zoomBlur(fromTex,uv,p*.35),zoomBlur(toTex,uv,(1.-p)*.35),p); }
else if(kind==4 || kind==5){ float depth=texture2D(depthTex,uv).r; float ordering=kind==4?depth:1.-depth; float reveal=smoothstep(p-.16,p+.16,ordering); outColor=mix(texture2D(fromTex,uv),texture2D(toTex,uv),reveal); }
else { outColor=mix(texture2D(fromTex,uv),texture2D(toTex,uv),p); } gl_FragColor=outColor; }`;

const KIND_INDEX: Record<TransitionKind, number> = { crossfade: 0, glitch: 1, rgb_split: 2, zoom_blur: 3, depth_person_through: 4, depth_background_peel: 5, morph_cut: 0 };

interface TransitionPreviewCanvasProps {
  fromVideo: HTMLVideoElement;
  toVideo: HTMLVideoElement;
  progress: number;
  kind: TransitionKind;
  depthMap?: HTMLImageElement | HTMLVideoElement;
  /** Optional trusted GLSL fragment body. It must use the standard uniforms in COMMON. */
  customFragment?: string;
  width: number;
  height: number;
  className?: string;
}

function compile(gl: WebGLRenderingContext, type: number, source: string) { const shader = gl.createShader(type); if (!shader) throw new Error("Shader allocation failed"); gl.shaderSource(shader, source); gl.compileShader(shader); if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? "Shader compile error"); return shader; }
function texture(gl: WebGLRenderingContext, unit: number) { const item = gl.createTexture(); if (!item) throw new Error("Texture allocation failed"); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, item); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); return item; }

/** WebGL preview consumes the same TransitionKind/shader id that the backend compiles to xfade or gltransition. */
export function TransitionPreviewCanvas({ fromVideo, toVideo, progress, kind, depthMap, customFragment, width, height, className }: TransitionPreviewCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current; const gl = canvas?.getContext("webgl"); if (!canvas || !gl || fromVideo.readyState < 2 || toVideo.readyState < 2) return;
    const program = gl.createProgram(); if (!program) return;
    try {
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX)); gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, customFragment ?? COMMON)); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Program link error");
      gl.useProgram(program); const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
      const position = gl.getAttribLocation(program, "p"); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      const upload = (source: TexImageSource, unit: number) => { texture(gl, unit); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source); gl.uniform1i(gl.getUniformLocation(program, unit === 0 ? "fromTex" : unit === 1 ? "toTex" : "depthTex"), unit); };
      upload(fromVideo, 0); upload(toVideo, 1); upload(depthMap && (!(depthMap instanceof HTMLVideoElement) || depthMap.readyState >= 2) ? depthMap : fromVideo, 2);
      gl.uniform1f(gl.getUniformLocation(program, "progress"), progress); gl.uniform1i(gl.getUniformLocation(program, "kind"), KIND_INDEX[kind]); gl.viewport(0, 0, width, height); gl.drawArrays(gl.TRIANGLES, 0, 6);
    } catch (error) { console.warn("Transition shader preview failed", error); }
    return () => gl.deleteProgram(program);
  }, [customFragment, depthMap, fromVideo, height, kind, progress, toVideo, width]);
  return <canvas ref={ref} width={width} height={height} className={className ?? "h-full w-full"} />;
}
