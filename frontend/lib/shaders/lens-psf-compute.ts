/** WebGPU tile-convolution compute stage. Input/output are sRGB; convolution runs in linear light. */
export const LENS_PSF_COMPUTE_WGSL = /* wgsl */ `
const WORKGROUP: u32 = 16u;
const MAX_RADIUS: i32 = 7;
const TILE: u32 = 30u;

struct LensParams {
  dimensions: vec2<u32>,
  kernelRadius: u32,
  _pad: u32,
  opticalCenter: vec2<f32>,
  chromaticOffsetPx: f32,
  fieldMtfFalloff: f32,
};

@group(0) @binding(0) var inputFrame: texture_2d<f32>;
@group(0) @binding(1) var outputFrame: texture_storage_2d<rgba8unorm, write>;
@group(0) @binding(2) var<storage, read> psf: array<f32>;
@group(0) @binding(3) var<uniform> params: LensParams;
var<workgroup> tile: array<vec4<f32>, 900>;

fn srgbToLinear(c: vec3<f32>) -> vec3<f32> {
  let low = c / 12.92;
  let high = pow((c + vec3<f32>(0.055)) / 1.055, vec3<f32>(2.4));
  return select(high, low, c <= vec3<f32>(0.04045));
}
fn linearToSrgb(c: vec3<f32>) -> vec3<f32> {
  let low = c * 12.92;
  let high = 1.055 * pow(max(c, vec3<f32>(0.0)), vec3<f32>(1.0 / 2.4)) - 0.055;
  return select(high, low, c <= vec3<f32>(0.0031308));
}
fn clamped(coord: vec2<i32>) -> vec2<i32> {
  return clamp(coord, vec2<i32>(0), vec2<i32>(params.dimensions) - vec2<i32>(1));
}
fn tileAt(coord: vec2<i32>) -> vec3<f32> {
  let index = u32(coord.y) * TILE + u32(coord.x);
  return tile[index].rgb;
}

@compute @workgroup_size(16, 16, 1)
fn main(
  @builtin(workgroup_id) workgroup: vec3<u32>,
  @builtin(local_invocation_id) local: vec3<u32>,
  @builtin(global_invocation_id) global: vec3<u32>,
) {
  let origin = vec2<i32>(workgroup.xy * WORKGROUP) - vec2<i32>(MAX_RADIUS);
  let linearInvocation = local.y * WORKGROUP + local.x;
  for (var index = linearInvocation; index < TILE * TILE; index = index + WORKGROUP * WORKGROUP) {
    let tx = i32(index % TILE);
    let ty = i32(index / TILE);
    let raw = textureLoad(inputFrame, clamped(origin + vec2<i32>(tx, ty)), 0);
    tile[index] = vec4<f32>(srgbToLinear(raw.rgb), raw.a);
  }
  workgroupBarrier();
  if (global.x >= params.dimensions.x || global.y >= params.dimensions.y) { return; }

  let radius = i32(params.kernelRadius);
  let localPixel = vec2<i32>(local.xy) + vec2<i32>(MAX_RADIUS);
  let field = (vec2<f32>(global.xy) - params.opticalCenter) / max(vec2<f32>(params.dimensions), vec2<f32>(1.0));
  let radial = length(field) * 2.0;
  let direction = normalize(field + vec2<f32>(0.000001));
  let channelShift = vec2<i32>(round(direction * params.chromaticOffsetPx * radial));
  var red = 0.0; var green = 0.0; var blue = 0.0; var weight = 0.0;
  for (var ky = -MAX_RADIUS; ky <= MAX_RADIUS; ky = ky + 1) {
    for (var kx = -MAX_RADIUS; kx <= MAX_RADIUS; kx = kx + 1) {
      if (abs(kx) > radius || abs(ky) > radius) { continue; }
      let kernelIndex = u32((ky + radius) * (2 * radius + 1) + (kx + radius));
      let w = psf[kernelIndex];
      let base = localPixel + vec2<i32>(kx, ky);
      red += tileAt(clamp(base + channelShift, vec2<i32>(0), vec2<i32>(i32(TILE) - 1))).r * w;
      green += tileAt(base).g * w;
      blue += tileAt(clamp(base - channelShift, vec2<i32>(0), vec2<i32>(i32(TILE) - 1))).b * w;
      weight += w;
    }
  }
  let source = tileAt(localPixel).rgb;
  let convolved = vec3<f32>(red, green, blue) / max(weight, 0.00001);
  let mtfMix = clamp(radial * params.fieldMtfFalloff, 0.0, 1.0);
  textureStore(outputFrame, vec2<i32>(global.xy), vec4<f32>(linearToSrgb(mix(source, convolved, mtfMix)), 1.0));
}`;
