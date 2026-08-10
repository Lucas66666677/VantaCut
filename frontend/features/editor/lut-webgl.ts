export interface ParsedCubeLut {
  size: number;
  values: Uint8Array;
}

/** Parse the RGB-fastest .cube ordering emitted by the backend LUT generator. */
export function parseCubeLut(cubeText: string): ParsedCubeLut {
  const data: number[] = [];
  let size = 0;
  for (const rawLine of cubeText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith("TITLE") || line.startsWith("DOMAIN_")) continue;
    if (line.startsWith("LUT_3D_SIZE")) {
      size = Number(line.split(/\s+/)[1]);
      continue;
    }
    const channels = line.split(/\s+/).map(Number);
    if (channels.length === 3 && channels.every(Number.isFinite)) {
      data.push(...channels.map((channel) => Math.round(Math.min(1, Math.max(0, channel)) * 255)));
    }
  }
  if (!Number.isInteger(size) || size < 2 || data.length !== size ** 3 * 3) {
    throw new Error("Invalid .cube LUT data");
  }
  return { size, values: new Uint8Array(data) };
}

export function uploadCubeLut(gl: WebGL2RenderingContext, lut: ParsedCubeLut): WebGLTexture {
  const texture = gl.createTexture();
  if (!texture) throw new Error("Unable to allocate LUT texture");
  gl.bindTexture(gl.TEXTURE_3D, texture);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE);
  gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB8, lut.size, lut.size, lut.size, 0, gl.RGB, gl.UNSIGNED_BYTE, lut.values);
  return texture;
}

/** CPU fallback for compact A/B previews. The main video preview can use the WebGL texture above. */
export function drawCubeLut(canvas: HTMLCanvasElement, source: CanvasImageSource, lut: ParsedCubeLut, intensity = 1) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return;
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const weight = Math.max(0, Math.min(1, intensity)); const { size, values } = lut;
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const originalRed = image.data[offset]; const originalGreen = image.data[offset + 1]; const originalBlue = image.data[offset + 2];
    const red = Math.round(originalRed / 255 * (size - 1)); const green = Math.round(originalGreen / 255 * (size - 1)); const blue = Math.round(originalBlue / 255 * (size - 1));
    const lutOffset = (blue * size * size + green * size + red) * 3;
    image.data[offset] = Math.round(originalRed * (1 - weight) + values[lutOffset] * weight);
    image.data[offset + 1] = Math.round(originalGreen * (1 - weight) + values[lutOffset + 1] * weight);
    image.data[offset + 2] = Math.round(originalBlue * (1 - weight) + values[lutOffset + 2] * weight);
  }
  context.putImageData(image, 0, 0);
}

/** WebGL2 fragment shader: bind proxy/video frame to uVideo and .cube data to uLut. */
export const LUT_FRAGMENT_SHADER = `#version 300 es
precision highp float;
uniform sampler2D uVideo;
uniform highp sampler3D uLut;
in vec2 vUv;
out vec4 outColor;
void main() {
  vec4 source = texture(uVideo, vUv);
  vec3 graded = texture(uLut, clamp(source.rgb, 0.0, 1.0)).rgb;
  outColor = vec4(graded, source.a);
}`;
