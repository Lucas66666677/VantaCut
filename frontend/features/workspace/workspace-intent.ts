import type { WorkspaceIntent } from "@/types/workspace";

/** Fast, private first-pass classifier; replace with a structured Agent call when needed. */
export function classifyWorkspaceIntent(input: string): WorkspaceIntent {
  const text = input.trim().toLocaleLowerCase();
  if (/(調色|色彩|色輪|lut|look|color|grade|scope|示波器)/i.test(text)) {
    return { mode: "color", modules: ["timeline", "inspector", "color_wheels", "scopes"], summary: "已切換為精細調色工作區。" };
  }
  if (/(混音|音訊|聲音|降噪|音量|lufs|audio|mix|eq)/i.test(text)) {
    return { mode: "audio", modules: ["timeline", "inspector", "audio_mixer"], summary: "已切換為音訊混音工作區。" };
  }
  if (/(剪輯|粗剪|時間軸|b-roll|字幕|trim|edit|cut)/i.test(text)) {
    return { mode: "editing", modules: ["timeline", "inspector"], summary: "已開啟剪輯工作區。" };
  }
  return { mode: "welcome", modules: [], summary: "告訴我想完成什麼；例如：幫我精細調色。" };
}
