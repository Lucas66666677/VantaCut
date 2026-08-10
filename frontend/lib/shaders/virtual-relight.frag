#version 300 es
precision highp float;

// Depth is a relative monocular map, normalised per analysed shot; it is not metric Z.
uniform sampler2D uFrame;
uniform sampler2D uDepth;
uniform vec2 uResolution;
uniform int uLightCount;
uniform vec4 uLightPositionIntensity[4]; // xy = screen UV, z = relative depth, w = intensity
uniform vec4 uLightColorRadius[4];       // rgb = linear-light colour, a = relative radius
uniform vec2 uLightVolumeShadow[4];      // x = volumetric strength, y = shadow strength
uniform float uAmbient;
in vec2 vUv;
out vec4 outColor;

vec3 srgbToLinear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
vec3 linearToSrgb(vec3 c) { return pow(max(c, 0.0), vec3(1.0 / 2.2)); }

vec3 normalFromDepth(vec2 uv) {
  vec2 px = 1.0 / uResolution;
  float l = texture(uDepth, uv - vec2(px.x, 0.0)).r;
  float r = texture(uDepth, uv + vec2(px.x, 0.0)).r;
  float d = texture(uDepth, uv - vec2(0.0, px.y)).r;
  float u = texture(uDepth, uv + vec2(0.0, px.y)).r;
  return normalize(vec3(-(r - l) * 2.0, -(u - d) * 2.0, 1.0));
}

float rayMarchShadow(vec2 uv, float depth, vec2 lampUv, float strength) {
  float occlusion = 0.0;
  // Screen-space ray marching cannot see off-screen/occluded geometry; it is a fast preview proxy.
  for (int step = 1; step <= 12; ++step) {
    float t = float(step) / 13.0;
    float sampleDepth = texture(uDepth, mix(uv, lampUv, t)).r;
    occlusion = max(occlusion, smoothstep(.025, .15, sampleDepth - depth) * (1.0 - t));
  }
  return 1.0 - occlusion * strength;
}

float volumetricBeam(vec2 uv, vec2 lampUv, float depth, float strength) {
  float integral = 0.0;
  for (int step = 1; step <= 12; ++step) {
    float t = float(step) / 12.0;
    vec2 p = mix(lampUv, uv, t);
    float edge = abs(texture(uDepth, p + vec2(1.0 / uResolution.x, 0.0)).r - texture(uDepth, p).r);
    integral += (.08 + edge * 4.0) * (1.0 - t);
  }
  return integral / 12.0 * strength * smoothstep(1.25, .0, distance(uv, lampUv));
}

void main() {
  vec3 lit = srgbToLinear(texture(uFrame, vUv).rgb);
  float depth = texture(uDepth, vUv).r;
  vec3 normal = normalFromDepth(vUv);
  vec3 surface = vec3(vUv * 2.0 - 1.0, 1.0 - depth);
  for (int index = 0; index < 4; ++index) {
    if (index >= uLightCount) break;
    vec4 positionIntensity = uLightPositionIntensity[index];
    vec3 lamp = vec3(positionIntensity.xy * 2.0 - 1.0, 1.0 - positionIntensity.z);
    vec3 ray = lamp - surface;
    float distanceSquared = max(dot(ray, ray), .015);
    vec3 lightDirection = normalize(ray);
    float diffuse = max(dot(normal, lightDirection), 0.0);
    float attenuation = 1.0 / (1.0 + distanceSquared / max(uLightColorRadius[index].a * uLightColorRadius[index].a, .01));
    float shadow = rayMarchShadow(vUv, depth, positionIntensity.xy, uLightVolumeShadow[index].y);
    float volume = volumetricBeam(vUv, positionIntensity.xy, depth, uLightVolumeShadow[index].x);
    lit += uLightColorRadius[index].rgb * (diffuse * attenuation * shadow * positionIntensity.w + volume * attenuation);
  }
  outColor = vec4(linearToSrgb(clamp(lit + uAmbient, 0.0, 1.0)), 1.0);
}
