"use client";

import { useMemo, useState } from "react";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Emotion = "neutral" | "excited" | "calm" | "serious" | "warm" | "sad";

export interface EditableTranscriptCue {
  id: string;
  start_time: number;
  end_time: number;
  text: string;
}

interface AiDubEditorProps {
  projectId: string;
  timelineId: string;
  userId: string;
  sourceMediaAssetId: string;
  cues: EditableTranscriptCue[];
  readyVoiceProfiles: Array<{ id: string; name: string }>;
}

/** Transcript-side inspector: replacement stays asynchronous and requires explicit consent every time. */
export function AiDubEditor({ projectId, timelineId, userId, sourceMediaAssetId, cues, readyVoiceProfiles }: AiDubEditorProps) {
  const [cueId, setCueId] = useState(cues[0]?.id ?? "");
  const cue = useMemo(() => cues.find((item) => item.id === cueId) ?? cues[0], [cueId, cues]);
  const [text, setText] = useState(cue?.text ?? "");
  const [voiceProfileId, setVoiceProfileId] = useState(readyVoiceProfiles[0]?.id ?? "");
  const [emotion, setEmotion] = useState<Emotion>("neutral");
  const [tempo, setTempo] = useState(1);
  const [consent, setConsent] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const onCueChange = (nextId: string) => { const next = cues.find((item) => item.id === nextId); setCueId(nextId); setText(next?.text ?? ""); };
  const createProfile = async () => {
    if (!consent) { setStatus("請先確認你已取得講者授權。"); return; }
    setStatus("正在建立聲音 Profile…");
    const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/projects/${projectId}/voice-profiles`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_media_asset_id: sourceMediaAssetId, name: "Project speaker", consent_confirmed: true }) });
    setStatus(response.ok ? "聲音 Profile 已排入背景處理；完成後重新載入此面板。" : "無法建立聲音 Profile。");
  };
  const generate = async () => {
    if (!cue || !voiceProfileId || !consent) { setStatus("請選擇字幕、已完成的 Profile，並確認授權。"); return; }
    setStatus("正在生成 AI 補錄…");
    const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/voice-replacements`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ voice_profile_id: voiceProfileId, cue_id: cue.id, replacement_text: text, emotion, tempo, consent_confirmed: true }) });
    setStatus(response.ok ? "AI 補錄已排入背景生成；完成後可在時間軸預覽。" : "AI 補錄請求失敗。");
  };

  return <section className="rounded-xl border border-violet-500/30 bg-zinc-900 p-4 text-sm text-zinc-100">
    <div className="flex items-center justify-between"><h3 className="font-semibold">AI 補錄</h3><span className="text-xs text-violet-300">Voice Clone / TTS</span></div>
    <label className="mt-3 block text-xs text-zinc-400">字幕片段<select value={cueId} onChange={(event) => onCueChange(event.target.value)} className="mt-1 w-full rounded bg-zinc-950 p-2 text-zinc-100">{cues.map((item) => <option key={item.id} value={item.id}>{item.start_time.toFixed(2)}s · {item.text}</option>)}</select></label>
    <label className="mt-3 block text-xs text-zinc-400">改寫文字<textarea value={text} onChange={(event) => setText(event.target.value)} className="mt-1 min-h-20 w-full rounded bg-zinc-950 p-2 text-sm text-zinc-100" /></label>
    <div className="mt-3 grid grid-cols-2 gap-2"><label className="text-xs text-zinc-400">情緒<select value={emotion} onChange={(event) => setEmotion(event.target.value as Emotion)} className="mt-1 w-full rounded bg-zinc-950 p-2 text-zinc-100">{(["neutral", "excited", "calm", "serious", "warm", "sad"] as Emotion[]).map((item) => <option key={item}>{item}</option>)}</select></label><label className="text-xs text-zinc-400">Tempo {tempo.toFixed(2)}×<input className="mt-2 w-full" type="range" min="0.8" max="1.25" step="0.01" value={tempo} onChange={(event) => setTempo(Number(event.target.value))} /></label></div>
    <label className="mt-3 flex gap-2 text-xs text-amber-200"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />我已取得此講者的明確聲音複製與合成授權。</label>
    <div className="mt-3 flex gap-2"><button type="button" onClick={createProfile} className="rounded border border-violet-400 px-3 py-2 text-xs">建立 Voice Profile</button><select value={voiceProfileId} onChange={(event) => setVoiceProfileId(event.target.value)} className="min-w-0 flex-1 rounded bg-zinc-950 px-2 text-xs text-zinc-100">{readyVoiceProfiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" onClick={generate} className="rounded bg-violet-500 px-3 py-2 text-xs font-medium">生成補錄</button></div>
    {status && <p className="mt-3 text-xs text-zinc-300">{status}</p>}
  </section>;
}
