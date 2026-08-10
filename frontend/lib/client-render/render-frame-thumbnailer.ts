/**
 * Samples the source proxy at FFmpeg's reported output timestamp. Keeping this
 * at 5 fps makes the export window feel live without competing with the encoder.
 */
export class RenderFrameThumbnailer {
  private video = document.createElement("video");
  private canvas = document.createElement("canvas");
  private lastSampleAt = -Infinity;
  private sourceUrl: string;

  constructor(file: File) {
    this.sourceUrl = URL.createObjectURL(file);
    this.video.muted = true; this.video.playsInline = true; this.video.preload = "auto"; this.video.src = this.sourceUrl;
    this.canvas.width = 320; this.canvas.height = 180;
  }

  async sample(timeSeconds: number): Promise<string | null> {
    const now = performance.now(); if (now - this.lastSampleAt < 200) return null;
    this.lastSampleAt = now;
    await new Promise<void>((resolve) => {
      if (this.video.readyState >= HTMLMediaElement.HAVE_METADATA) resolve();
      else this.video.addEventListener("loadedmetadata", () => resolve(), { once: true });
    });
    this.video.currentTime = Math.max(0, Math.min(Math.max(0, this.video.duration - .05), timeSeconds));
    await new Promise<void>((resolve) => this.video.addEventListener("seeked", () => resolve(), { once: true }));
    const context = this.canvas.getContext("2d", { alpha: false }); if (!context) return null;
    const ratio = this.video.videoWidth / Math.max(1, this.video.videoHeight);
    this.canvas.width = 320; this.canvas.height = Math.max(96, Math.round(320 / Math.max(.1, ratio)));
    context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    return this.canvas.toDataURL("image/jpeg", .58);
  }

  dispose(): void { this.video.removeAttribute("src"); this.video.load(); URL.revokeObjectURL(this.sourceUrl); }
}
