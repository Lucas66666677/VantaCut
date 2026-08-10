/**
 * WebGL2 ACES preview path:
 * 1) upload an OCIO-baked camera-log -> ACEScct .cube as uCameraToAcescct;
 * 2) convert ACEScct to linear AP1-like working values;
 * 3) apply an SDR preview tone map for ordinary browser canvases.
 *
 * For HDR-capable browser/canvas pipelines, replace `acesFitted` with a second
 * OCIO-baked ACEScct -> Rec.2100 PQ/HLG display LUT and preserve 10-bit output.
 */
export const OCIO_ACES_PREVIEW_FRAGMENT_SHADER = `#version 300 es
precision highp float;

uniform sampler2D uVideo;
uniform highp sampler3D uCameraToAcescct;
uniform float uExposure;
in vec2 vUv;
out vec4 outColor;

vec3 acescctToLinear(vec3 encoded) {
  const float cut = 0.155251141552511;
  vec3 low = (encoded - 0.0729055341958355) / 10.5402377416545;
  vec3 high = exp2(encoded * 17.52 - 9.72);
  return mix(low, high, step(vec3(cut), encoded));
}

vec3 acesFitted(vec3 color) {
  // Narkowicz ACES fitted curve: SDR preview only, not a replacement for an OCIO ODT.
  const float a = 2.51;
  const float b = 0.03;
  const float c = 2.43;
  const float d = 0.59;
  const float e = 0.14;
  return clamp((color * (a * color + b)) / (color * (c * color + d) + e), 0.0, 1.0);
}

vec3 linearToSrgb(vec3 linear) {
  vec3 low = linear * 12.92;
  vec3 high = 1.055 * pow(max(linear, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
  return mix(low, high, step(vec3(0.0031308), linear));
}

void main() {
  vec4 cameraEncoded = texture(uVideo, vUv);
  vec3 acescct = texture(uCameraToAcescct, clamp(cameraEncoded.rgb, 0.0, 1.0)).rgb;
  vec3 sceneLinear = acescctToLinear(acescct) * exp2(uExposure);
  outColor = vec4(linearToSrgb(acesFitted(sceneLinear)), cameraEncoded.a);
}`;
