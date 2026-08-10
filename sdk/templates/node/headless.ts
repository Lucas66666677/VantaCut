/** Ergonomic facade layered over the generated TypeScript OpenAPI client. */
export class VideoAPI {
  constructor(private readonly client: AIEditorClient) {}
  roughCut(url: string, instructions: Record<string, unknown> = {}, idempotencyKey = crypto.randomUUID()) { return this.client.submit("rough-cut", url, instructions, idempotencyKey); }
  render(url: string, instructions: Record<string, unknown>, idempotencyKey = crypto.randomUUID()) { return this.client.submit("render", url, instructions, idempotencyKey); }
}

export class AIEditorClient {
  readonly video = new VideoAPI(this);
  constructor(private readonly apiKey: string, private readonly baseUrl = "https://api.example.com") {}
  async submit(operation: "rough-cut" | "render", source_url: string, instructions: Record<string, unknown>, idempotencyKey: string) {
    const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}/api/v1/platform/v1/videos/${operation}`, { method: "POST", headers: { "Content-Type": "application/json", "X-API-Key": this.apiKey, "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ source_url, instructions }) });
    if (!response.ok) throw new Error(`Platform API ${response.status}: ${await response.text()}`);
    return response.json();
  }
  async getJob(jobId: string) { const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}/api/v1/platform/v1/jobs/${jobId}`, { headers: { "X-API-Key": this.apiKey } }); if (!response.ok) throw new Error(`Platform API ${response.status}`); return response.json(); }
}
