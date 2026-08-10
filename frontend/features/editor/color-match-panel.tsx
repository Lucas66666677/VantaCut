"use client";

import { useState } from "react";

import { BeforeAfterColorMatch } from "@/features/editor/before-after-color-match";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface ColorMatchPanelProps {
  timelineId: string;
  projectId: string;
  userId: string;
  sourceAssetId: string;
  previewSource: CanvasImageSource | null;
  previewFrameVersion?: number;
}

export function ColorMatchPanel({ timelineId, projectId, userId, sourceAssetId, previewSource, previewFrameVersion = 0 }: ColorMatchPanelProps) {
  const [file, setFile] = useState<File | null>(null); const [pending, setPending] = useState(false); const [lutUrl, setLutUrl] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  const create = async () => {
    if (!file) { setError("請先選擇一張參考色調截圖。"); return; }
    setPending(true); setError(null);
    try {
      const uploadResponse = await fetch(`${API_URL}/api/v1/media/upload-url`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId, filename: file.name, size_bytes: file.size, content_type: file.type || "image/jpeg", media_type: "image" }) });
      const upload = await uploadResponse.json() as { asset_id?: string; upload_url?: string; required_headers?: Record<string, string>; detail?: string };
      if (!uploadResponse.ok || !upload.asset_id || !upload.upload_url) throw new Error(upload.detail ?? "無法建立參考圖上傳")
      const put = await fetch(upload.upload_url, { method: "PUT", headers: upload.required_headers, body: file });
      if (!put.ok) throw new Error("參考圖上傳失敗");
      const confirm = await fetch(`${API_URL}/api/v1/media/confirm-upload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: upload.asset_id }) });
      if (!confirm.ok) throw new Error("參考圖確認失敗");
      const matchResponse = await fetch(`${API_URL}/api/v1/timelines/${timelineId}/color-match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, reference_image_asset_id: upload.asset_id, source_asset_id: sourceAssetId, intensity: 100, lut_size: 33 }) });
      const match = await matchResponse.json() as { lut_download_url?: string; detail?: string };
      if (!matchResponse.ok || !match.lut_download_url) throw new Error(match.detail ?? "無法產生色彩匹配 LUT");
      setLutUrl(match.lut_download_url);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法建立色彩匹配"); } finally { setPending(false); }
  };
  return <section className="rounded-xl border border-fuchsia-400/30 bg-zinc-950 p-4 text-zinc-100"><h2 className="text-sm font-semibold">一鍵色彩匹配</h2><p className="mt-1 text-xs text-zinc-400">上傳喜歡的截圖，系統會用來源影格與參考直方圖生成專屬 3D LUT。</p><label className="mt-3 block cursor-pointer rounded-lg border border-dashed border-zinc-700 bg-zinc-900 p-3 text-xs text-zinc-300 hover:border-fuchsia-300"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setFile(event.target.files?.[0] ?? null)} className="sr-only" />{file ? `參考圖：${file.name}` : "選擇參考截圖（PNG、JPG、WebP）"}</label><button type="button" disabled={pending || !file} onClick={() => void create()} className="mt-3 rounded bg-fuchsia-300 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-50">{pending ? "正在分析色調…" : "生成專屬色彩匹配"}</button><BeforeAfterColorMatch previewSource={previewSource} lutUrl={lutUrl} frameVersion={previewFrameVersion} />{error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}</section>;
}
