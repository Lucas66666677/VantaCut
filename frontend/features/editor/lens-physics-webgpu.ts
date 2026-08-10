import { LENS_PSF_COMPUTE_WGSL } from "@/lib/shaders/lens-psf-compute";

// Keep the feature optional: stable TypeScript DOM packages do not all ship
// WebGPU declarations yet. Runtime support is still checked in `create`.
type WebGPUHandle = any; // eslint-disable-line @typescript-eslint/no-explicit-any
const GPU_TEXTURE_USAGE = { COPY_SRC: 0x01, COPY_DST: 0x02, TEXTURE_BINDING: 0x04, STORAGE_BINDING: 0x08, RENDER_ATTACHMENT: 0x10 } as const;
const GPU_BUFFER_USAGE = { COPY_DST: 0x08, UNIFORM: 0x40, STORAGE: 0x80 } as const;

export interface LensPhysicsPreviewParameters {
  kernelRadius: number;
  chromaticOffsetPx: number;
  fieldMtfFalloff: number;
  opticalCenter?: { x: number; y: number };
}

/**
 * GPU preview executor. It partitions the frame into 16×16 outputs and a 30×30
 * shared-memory tile (16 pixels plus 7-pixel halo on each side), reducing global
 * texture reads from O(kernel²) per channel to one cooperative tile load.
 */
export class LensPhysicsWebGPUPreview {
  private constructor(
    private readonly device: WebGPUHandle,
    private readonly context: WebGPUHandle,
    private readonly canvasFormat: WebGPUHandle,
    private readonly pipeline: WebGPUHandle,
    private readonly paramsBuffer: WebGPUHandle,
    private readonly kernelBuffer: WebGPUHandle,
  ) {}

  static async create(canvas: HTMLCanvasElement): Promise<LensPhysicsWebGPUPreview | null> {
    const gpu = (navigator as Navigator & { gpu?: WebGPUHandle }).gpu;
    if (!gpu) return null;
    const adapter = await gpu.requestAdapter({ powerPreference: "high-performance" });
    if (!adapter) return null;
    const device = await adapter.requestDevice();
    const context = canvas.getContext("webgpu" as never) as unknown as WebGPUHandle | null;
    if (!context) return null;
    const canvasFormat = gpu.getPreferredCanvasFormat();
    context.configure({ device, format: canvasFormat, alphaMode: "premultiplied", usage: GPU_TEXTURE_USAGE.COPY_DST | GPU_TEXTURE_USAGE.RENDER_ATTACHMENT });
    const pipeline = device.createComputePipeline({ layout: "auto", compute: { module: device.createShaderModule({ code: LENS_PSF_COMPUTE_WGSL }), entryPoint: "main" } });
    return new LensPhysicsWebGPUPreview(
      device, context, canvasFormat, pipeline,
      device.createBuffer({ size: 32, usage: GPU_BUFFER_USAGE.UNIFORM | GPU_BUFFER_USAGE.COPY_DST }),
      device.createBuffer({ size: 15 * 15 * Float32Array.BYTES_PER_ELEMENT, usage: GPU_BUFFER_USAGE.STORAGE | GPU_BUFFER_USAGE.COPY_DST }),
    );
  }

  render(source: CanvasImageSource, width: number, height: number, psfKernel: Float32Array, parameters: LensPhysicsPreviewParameters): void {
    const radius = Math.max(1, Math.min(7, Math.floor(parameters.kernelRadius)));
    const expected = (radius * 2 + 1) ** 2;
    if (psfKernel.length !== expected) throw new Error(`Expected a ${(radius * 2 + 1)}×${(radius * 2 + 1)} PSF kernel`);
    const input = this.device.createTexture({ size: [width, height], format: "rgba8unorm", usage: GPU_TEXTURE_USAGE.COPY_DST | GPU_TEXTURE_USAGE.TEXTURE_BINDING });
    const output = this.device.createTexture({ size: [width, height], format: "rgba8unorm", usage: GPU_TEXTURE_USAGE.STORAGE_BINDING | GPU_TEXTURE_USAGE.COPY_SRC });
    this.device.queue.copyExternalImageToTexture({ source }, { texture: input }, [width, height]);
    this.device.queue.writeBuffer(this.kernelBuffer, 0, psfKernel);
    const uniform = new ArrayBuffer(32); const view = new DataView(uniform); const center = parameters.opticalCenter ?? { x: width / 2, y: height / 2 };
    view.setUint32(0, width, true); view.setUint32(4, height, true); view.setUint32(8, radius, true);
    view.setFloat32(16, center.x, true); view.setFloat32(20, center.y, true); view.setFloat32(24, parameters.chromaticOffsetPx, true); view.setFloat32(28, parameters.fieldMtfFalloff, true);
    this.device.queue.writeBuffer(this.paramsBuffer, 0, uniform);
    const bindGroup = this.device.createBindGroup({ layout: this.pipeline.getBindGroupLayout(0), entries: [
      { binding: 0, resource: input.createView() }, { binding: 1, resource: output.createView() },
      { binding: 2, resource: { buffer: this.kernelBuffer } }, { binding: 3, resource: { buffer: this.paramsBuffer } },
    ] });
    const encoder = this.device.createCommandEncoder(); const pass = encoder.beginComputePass(); pass.setPipeline(this.pipeline); pass.setBindGroup(0, bindGroup); pass.dispatchWorkgroups(Math.ceil(width / 16), Math.ceil(height / 16)); pass.end();
    encoder.copyTextureToTexture({ texture: output }, { texture: this.context.getCurrentTexture() }, [width, height]);
    this.device.queue.submit([encoder.finish()]);
    void this.device.queue.onSubmittedWorkDone().finally(() => { input.destroy(); output.destroy(); });
  }
}
