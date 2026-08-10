#version 300 es
precision highp float;

uniform sampler2D uFrame;
uniform vec2 uResolution;
uniform float uTime;
uniform float uGrainAmount;
uniform float uHalationStrength;
uniform float uHalationThreshold;
uniform float uHalationRadius;
uniform float uApertureFactor;
uniform float uSphericalAberration;
uniform float uEdgeMtfFalloff;
uniform float uFocusBreathingScale;
in vec2 vUv;
out vec4 outColor;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7)) + uTime * 17.13) * 43758.5453123); }
vec3 toLinear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
vec3 toSrgb(vec3 c) { return pow(max(c, 0.0), vec3(1.0 / 2.2)); }

void main() {
  vec2 centered = (vUv - .5) / max(uFocusBreathingScale, .001) + .5;
  vec2 px = 1.0 / uResolution;
  vec3 base = toLinear(texture(uFrame, centered).rgb);
  float luma = dot(base, vec3(.2126, .7152, .0722));
  float high = smoothstep(uHalationThreshold, 1.0, luma);
  vec3 bloom = vec3(0.0);
  // 9 taps is the preview approximation; production uses a separable compute blur pyramid.
  for (int y = -1; y <= 1; ++y) for (int x = -1; x <= 1; ++x) {
    vec2 offset = vec2(float(x), float(y)) * px * uHalationRadius;
    vec3 sampleColor = toLinear(texture(uFrame, centered + offset).rgb);
    bloom += vec3(smoothstep(uHalationThreshold, 1.0, dot(sampleColor, vec3(.2126, .7152, .0722)))) / 9.0;
  }
  base += max(bloom - vec3(high * .35), 0.0) * vec3(1.0, .20, .07) * uHalationStrength;
  float r = length(vUv - .5) * 1.4142;
  vec2 outward = normalize(vUv - .5 + vec2(.00001));
  vec3 soft = toLinear(texture(uFrame, centered + outward * px * (1.0 + 3.0 * r * r) * uApertureFactor).rgb);
  float blurMix = clamp(uSphericalAberration * uApertureFactor * (.25 + .75 * r * r) + uEdgeMtfFalloff * r * r, 0.0, .92);
  base = mix(base, soft, blurMix);
  float grainWeight = .25 + .75 * pow(1.0 - clamp(luma, 0.0, 1.0), .65);
  float grain = (hash(gl_FragCoord.xy) - .5) + (hash(gl_FragCoord.xy * .37 + 19.0) - .5) * .22;
  base += grain * grainWeight * uGrainAmount * .035;
  outColor = vec4(toSrgb(base), 1.0);
}
