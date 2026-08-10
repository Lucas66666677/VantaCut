"use client";

import { useState } from "react";

interface ExportShareButtonProps { downloadUrl: string; filename: string; title?: string; text?: string; }

/** Uses the operating-system share sheet; installed Instagram/TikTok/LINE clients decide whether they accept the MP4. */
export function ExportShareButton({ downloadUrl, filename, title = "我的影片", text = "由 AI Video Editor 製作" }: ExportShareButtonProps) {
  const [message, setMessage] = useState<string | null>(null);
  const share = async () => {
    try {
      if (!navigator.share) throw new Error("此瀏覽器不支援系統分享");
      const blob = await (await fetch(downloadUrl)).blob(); const file = new File([blob], filename, { type: blob.type || "video/mp4" });
      if (navigator.canShare?.({ files: [file] })) await navigator.share({ title, text, files: [file] });
      else await navigator.share({ title, text, url: downloadUrl });
      setMessage("已開啟系統分享選單");
    } catch (cause) { if (cause instanceof DOMException && cause.name === "AbortError") return; setMessage(cause instanceof Error ? `${cause.message}，可改用下載按鈕。` : "分享失敗，請改用下載按鈕。"); }
  };
  return <div className="inline-flex flex-col items-start gap-1"><div className="flex gap-2"><a href={downloadUrl} download={filename} className="rounded bg-white px-3 py-2 text-xs font-bold text-zinc-950">下載 MP4</a><button type="button" onClick={() => void share()} className="rounded border border-zinc-600 px-3 py-2 text-xs font-semibold text-zinc-100">分享至 Instagram / TikTok / LINE</button></div>{message && <p className="text-xs text-zinc-400">{message}</p>}</div>;
}

