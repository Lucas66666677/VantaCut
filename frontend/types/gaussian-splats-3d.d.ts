declare module "@mkkellogg/gaussian-splats-3d" {
  export class Viewer {
    constructor(options?: Record<string, unknown>);
    camera: unknown;
    addSplatScene(url: string, options?: Record<string, unknown>): Promise<void>;
    start(): void;
    dispose?(): void;
  }
}
