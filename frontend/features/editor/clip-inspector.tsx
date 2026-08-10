"use client";

import { useEffect, useState } from "react";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { KeyframeEditor } from "@/features/editor/keyframe-editor";
import { SpeedCurvePanel } from "@/features/editor/speed-curve-panel";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import { ContextToolShelf, useWorkspaceContext, type WorkspaceTool } from "@/features/workspace/context-aware-workspace";
import type { TimelineClip } from "@/types/timeline";
import type { ClipLayout } from "@/types/timeline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface ClipInspectorProps {
  clip: TimelineClip | null;
  timelineId?: string;
  userId?: string;
}

export function ClipInspector({ clip, timelineId, userId }: ClipInspectorProps) {
  const setAudioEffect = useTimelineStore((state) => state.setAudioEffect);
  const setAudioGain = useTimelineStore((state) => state.setAudioGain);
  const resetAiModifiedProperty = useTimelineStore((state) => state.resetAiModifiedProperty);
  const beginOptimistic = useOptimisticEffectsStore((state) => state.begin);
  const attachTask = useOptimisticEffectsStore((state) => state.attachTask);
  const failOptimistic = useOptimisticEffectsStore((state) => state.fail);
  const removeForClipEffect = useOptimisticEffectsStore((state) => state.removeForClipEffect);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wetMix, setWetMix] = useState(72);
  const [morphCharacter, setMorphCharacter] = useState<"robot" | "monster" | "storybook">("robot");
  const [morphConsent, setMorphConsent] = useState(false);
  const [morphPending, setMorphPending] = useState(false);
  const [morphMessage, setMorphMessage] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [contextMessage, setContextMessage] = useState<string | null>(null);
  const workspaceContext = useWorkspaceContext(clip, timelineId, userId);

  useEffect(() => { if (clip) { setWetMix(72); setAdvanced(false); setContextMessage(null); } }, [clip?.id]);

  if (!clip) {
    return <aside className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 text-sm text-zinc-500">選取一個片段以調整屬性。</aside>;
  }

  const noiseReductionEnabled = clip.audio_effects.includes("noise_reduction");
  const studioSoundEnabled = clip.audio_effects.includes("studio_sound");
  const canPersist = Boolean(timelineId && !clip.id.startsWith("ai-clip-"));
  const modifiedProperties = Object.entries(clip.ai_modified_properties ?? {});
  const propertyLabel = (property: string) => ({
    audio_gain_db: "音量",
    "visual_adjustments.contrast": "對比",
    "visual_adjustments.brightness": "亮度",
    "visual_adjustments.filter_intensity": "濾鏡濃度",
  }[property] ?? property);

  const toggleNoiseReduction = async () => {
    const enabled = !noiseReductionEnabled;
    setError(null);
    setAudioEffect(clip.id, "noise_reduction", enabled);
    if (!enabled) { removeForClipEffect(clip.id, "noise_reduction"); if (!canPersist) return; }
    const optimisticId = enabled && canPersist ? beginOptimistic({ kind: "noise_reduction", clipId: clip.id, mediaAssetId: clip.source_asset_id, message: "已先套用乾淨人聲預覽，正在精修音訊。" }) : null;
    if (!canPersist) return;

    setIsSubmitting(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/timelines/${timelineId}/clips/${clip.id}/noise-reduction`,
        { method: enabled ? "POST" : "DELETE" },
      );
      const result = enabled ? await response.json() as { task_id?: string; detail?: string } : undefined;
      if (!response.ok) throw new Error(result?.detail ?? "無法更新降噪設定");
      if (enabled && result?.task_id && optimisticId) attachTask(optimisticId, result.task_id);
    } catch (requestError) {
      setAudioEffect(clip.id, "noise_reduction", !enabled);
      const message = requestError instanceof Error ? requestError.message : "無法更新降噪設定"; if (optimisticId) failOptimistic(optimisticId, message); setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleStudioSound = async () => {
    const enabled = !studioSoundEnabled;
    setError(null); setAudioEffect(clip.id, "studio_sound", enabled); if (!enabled) removeForClipEffect(clip.id, "studio_sound");
    const optimisticId = enabled && canPersist ? beginOptimistic({ kind: "studio_sound", clipId: clip.id, mediaAssetId: clip.source_asset_id, message: "已先套用錄音室質感預覽，正在清理環境聲。" }) : null;
    if (!canPersist) return;
    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/clips/${clip.id}/studio-sound`, enabled
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ wet_mix: wetMix }) }
        : { method: "DELETE" });
      const result = enabled ? await response.json() as { task_id?: string; detail?: string } : undefined;
      if (!response.ok) throw new Error(result?.detail ?? "無法更新 Studio Sound 設定");
      if (enabled && result?.task_id && optimisticId) attachTask(optimisticId, result.task_id);
    } catch (requestError) {
      setAudioEffect(clip.id, "studio_sound", !enabled);
      const message = requestError instanceof Error ? requestError.message : "無法更新 Studio Sound 設定"; if (optimisticId) failOptimistic(optimisticId, message); setError(message);
    } finally { setIsSubmitting(false); }
  };

  const persistWetMix = async () => {
    if (!studioSoundEnabled || !canPersist) return;
    setIsSubmitting(true); setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/clips/${clip.id}/studio-sound`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ wet_mix: wetMix }) });
      if (!response.ok) throw new Error("Studio Sound 尚未完成，請稍後再調整比例");
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "無法儲存乾濕比"); } finally { setIsSubmitting(false); }
  };

  const createVoiceMorph = async () => {
    if (!timelineId || !userId || !clip.source_asset_id || clip.track !== "main_video") return;
    if (!morphConsent) { setMorphMessage("請先確認你擁有或已取得此語音的轉換授權。"); return; }
    const outputStart = (clip as ClipLayout).displayStart ?? clip.timeline_start ?? clip.source_start;
    setMorphPending(true); setMorphMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/voice-morphs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, source_media_asset_id: clip.source_asset_id, source_start: clip.source_start, source_end: clip.source_end, timeline_start: outputStart, character_id: morphCharacter, consent_confirmed: true }) });
      const result = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(result.detail ?? "無法建立角色音色預覽");
      setMorphMessage("已送往雲端產生預覽；完成後會以派生音軌取代此段對白。");
    } catch (requestError) { setMorphMessage(requestError instanceof Error ? requestError.message : "無法建立角色音色預覽"); } finally { setMorphPending(false); }
  };

  const openContextTool = async (tool: WorkspaceTool) => {
    window.dispatchEvent(new CustomEvent("workspace-tool-intent", { detail: { tool, clipId: clip.id, timelineId } }));
    if (!timelineId || !userId || !clip.source_asset_id) { setContextMessage(`已將「${tool}」置頂。`); return; }
    try {
      if (tool === "screen_focus") await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/analyze-screen-focus`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, use_proxy: true }) });
      if (tool === "ar_arrows" || tool === "code_highlight") await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/mechanical-ar/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, media_asset_id: clip.source_asset_id, use_proxy: true }) });
      if (tool === "auto_reframe") await fetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/auto-reframe`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, detector_stride: 2, smoothing: .75, max_pan_speed_px_per_second: 720 }) });
      setContextMessage(`已啟用「${tool}」；你可以繼續編輯其他片段。`);
    } catch { setContextMessage(`已將「${tool}」置頂，請在工具面板中完成設定。`); }
  };

  return (
    <aside className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 text-sm">
      <h3 className="font-semibold text-zinc-100">Clip Inspector</h3>
      <p className="mt-1 truncate text-xs text-zinc-500">{clip.reason}</p>
      <ContextToolShelf context={workspaceContext} onTool={(tool) => void openContextTool(tool)} />
      <div className="mt-4 rounded-lg border border-zinc-700 bg-zinc-950/60 p-3">
        <div className="flex items-center justify-between text-xs text-zinc-200"><span>片段音量</span><b>{(clip.audio_gain_db ?? 0).toFixed(1)} dB</b></div>
        <div className="mt-2 flex items-center gap-2">
          <input aria-label="Clip audio gain" type="range" min="-24" max="24" step="0.5" value={clip.audio_gain_db ?? 0} onChange={(event) => setAudioGain(clip.id, Number(event.target.value))} className="w-full accent-amber-300" />
          {clip.ai_modified_properties?.audio_gain_db && <button type="button" title="AI 曾調整此值；點擊回復原始素材音量" onClick={() => resetAiModifiedProperty(clip.id, "audio_gain_db")} className="h-3 w-3 shrink-0 rounded-full bg-amber-300 shadow-[0_0_8px_rgba(253,224,71,.85)]" aria-label="回復原始音量" />}
        </div>
      </div>
      {modifiedProperties.length > 0 && <div className="mt-3 rounded-lg border border-amber-300/30 bg-amber-300/5 p-3"><p className="text-xs font-medium text-amber-100">AI 修改過的屬性</p><p className="mt-1 text-[11px] text-zinc-400">黃色點表示此值可獨立回復，不會影響其他剪輯決定。</p><div className="mt-2 space-y-1">{modifiedProperties.map(([property, change]) => <div key={property} className="flex items-center gap-2 text-xs"><button type="button" title={`回復 ${propertyLabel(property)} 的原始值`} onClick={() => resetAiModifiedProperty(clip.id, property)} className="h-2.5 w-2.5 rounded-full bg-amber-300 shadow-[0_0_7px_rgba(253,224,71,.8)]" /><span className="flex-1 text-zinc-200">{propertyLabel(property)}</span><span className="text-zinc-500">AI：{String(change.current_value)}</span><button type="button" onClick={() => resetAiModifiedProperty(clip.id, property)} className="text-amber-200 hover:text-amber-100">回復</button></div>)}</div></div>}
      <label className="mt-4 flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-zinc-700 px-3 py-2">
        <span>
          <span className="block text-zinc-200">AI 降噪與人聲增強</span>
          <span className="block text-xs text-zinc-500">高通、頻域降噪、低通與響度正規化</span>
        </span>
        <input
          type="checkbox"
          checked={noiseReductionEnabled}
          disabled={isSubmitting}
          onChange={toggleNoiseReduction}
          className="h-4 w-4 accent-violet-500"
        />
      </label>
      <button type="button" onClick={() => setAdvanced((value) => !value)} aria-expanded={advanced} className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-left text-xs text-zinc-300 transition hover:border-zinc-500"><span className="font-semibold">進階設定</span><span className="float-right text-zinc-500">{advanced ? "收起" : "展開曲線、音訊與關鍵幀"}</span></button>
      <div aria-hidden={!advanced} className="grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out" style={{ gridTemplateRows: advanced ? "1fr" : "0fr", opacity: advanced ? 1 : 0, marginTop: advanced ? ".75rem" : "0" }}>
      <div className="min-h-0 overflow-hidden"><div className="space-y-3">
      <div className="rounded-lg border border-cyan-400/35 bg-cyan-400/5 p-3">
        <label className="flex cursor-pointer items-center justify-between gap-3">
          <span><span className="block text-zinc-100">Studio Sound 錄音室音質</span><span className="block text-xs text-zinc-400">AI 人聲強化、風噪／引擎低頻與殘響抑制</span></span>
          <input type="checkbox" checked={studioSoundEnabled} disabled={isSubmitting} onChange={toggleStudioSound} className="h-4 w-4 accent-cyan-400" />
        </label>
        <label className={`mt-3 block text-xs ${studioSoundEnabled ? "text-zinc-200" : "text-zinc-600"}`}>Studio Sound 比例 <b>{wetMix}%</b><span className="float-right text-zinc-500">原聲 {100 - wetMix}%</span>
          <input type="range" min="0" max="100" value={wetMix} disabled={!studioSoundEnabled || isSubmitting} onChange={(event) => setWetMix(Number(event.target.value))} onPointerUp={() => void persistWetMix()} onKeyUp={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") void persistWetMix(); }} className="mt-2 w-full accent-cyan-300 disabled:opacity-35" />
        </label>
        <p className="mt-1 text-[11px] text-zinc-500">較低比例會保留海浪、街景等環境感；較高比例更接近 Podcast 人聲。</p>
      </div>
      {clip.track === "main_video" && <div className="mt-3 rounded-lg border border-fuchsia-400/35 bg-fuchsia-400/5 p-3"><div><span className="block text-zinc-100">情緒保留角色音色</span><span className="block text-xs text-zinc-400">保留原始 F0、音量起伏與笑聲節奏，只轉換為虛構角色音色。</span></div><div className="mt-2 grid grid-cols-3 gap-2">{([{ id: "robot", emoji: "🤖", label: "機器人" }, { id: "monster", emoji: "👹", label: "怪獸" }, { id: "storybook", emoji: "👧", label: "童話童聲" }] as const).map((role) => <button key={role.id} type="button" onClick={() => setMorphCharacter(role.id)} className={`rounded border px-1 py-2 text-xs ${morphCharacter === role.id ? "border-fuchsia-300 bg-fuchsia-300/15 text-white" : "border-zinc-700 text-zinc-300"}`}><span className="block text-lg">{role.emoji}</span>{role.label}</button>)}</div><label className="mt-3 flex items-start gap-2 text-[11px] text-zinc-300"><input type="checkbox" checked={morphConsent} onChange={(event) => setMorphConsent(event.target.checked)} className="mt-0.5 accent-fuchsia-300" />我確認擁有此語音，或已取得轉換與發布授權。</label><button type="button" disabled={morphPending || !clip.source_asset_id} onClick={() => void createVoiceMorph()} className="mt-3 rounded bg-fuchsia-300 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-45">{morphPending ? "正在產生角色預覽…" : "一鍵套用角色音色"}</button>{morphMessage && <p className={`mt-2 text-xs ${morphMessage.startsWith("已送") ? "text-emerald-300" : "text-amber-300"}`}>{morphMessage}</p>}</div>}
      <KeyframeEditor clip={clip} timelineId={timelineId} />
      <SpeedCurvePanel clip={clip} timelineId={timelineId} userId={userId} />
      </div></div></div>
      {!canPersist && <p className="mt-2 text-xs text-amber-300">請載入已儲存的 Timeline 後，才會提交背景降噪任務。</p>}
      {isSubmitting && <p className="mt-2 text-xs text-zinc-400">正在建立或更新音訊預覽…</p>}
      {contextMessage && <p className="mt-2 text-xs text-emerald-300">{contextMessage}</p>}
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
    </aside>
  );
}
