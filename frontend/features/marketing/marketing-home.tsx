"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

const capabilities = [
  ["01", "AI 粗剪", "自動標出靜音、贅詞與可省略段落；每個建議都先預覽，再由你決定。"],
  ["02", "多版型交付", "一次建立 16:9、9:16 與 1:1 版本，保留主角追蹤與平台安全區。"],
  ["03", "非破壞工作流", "原始素材不覆寫。裁切、濾鏡與音訊處理都能比較、撤回與重做。"],
] as const;

const workflow = ["匯入素材", "檢視 AI 提案", "確認並交付"];

export function MarketingHome() {
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  return (
    <main className="min-h-screen overflow-hidden bg-[var(--lr-color-background)] text-[var(--lr-color-text-primary)]">
      <a href="#main-content" className="sr-only z-50 rounded-md bg-[var(--lr-color-primary)] p-3 text-[var(--lr-color-text-inverse)] focus:not-sr-only focus:fixed focus:left-4 focus:top-4">跳至主要內容</a>
      <div aria-hidden className="pointer-events-none fixed inset-x-0 top-0 h-[34rem] bg-[radial-gradient(circle_at_72%_-20%,rgba(123,167,255,.14),transparent_52%)]" />

      <header className="relative z-10 border-b border-[var(--lr-color-border)] bg-[color:var(--lr-color-background)]/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 md:px-8">
          <Link href="/" className="flex items-center gap-3" aria-label="VantaCut 首頁">
            <Image src="/brand/lucirel-symbol-color-dark.svg" width={28} height={28} alt="" priority />
            <span className="text-sm font-semibold tracking-[.02em]">VantaCut</span>
            <span className="hidden border-l border-[var(--lr-color-border)] pl-3 text-[11px] font-medium uppercase tracking-[.18em] text-[var(--lr-color-text-muted)] sm:inline">by Lucirel</span>
          </Link>
          <nav aria-label="主要導覽" className="hidden items-center gap-6 text-sm text-[var(--lr-color-text-secondary)] md:flex">
            <a className="hover:text-[var(--lr-color-text-primary)]" href="#workflow">工作流程</a>
            <a className="hover:text-[var(--lr-color-text-primary)]" href="#features">功能</a>
            <a className="hover:text-[var(--lr-color-text-primary)]" href="#trust">資料控制</a>
          </nav>
          <Link href="/studio" className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-3.5 py-2 text-xs font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">開啟工作室</Link>
        </div>
      </header>

      <section id="main-content" className="relative z-10 mx-auto grid max-w-7xl gap-12 px-5 pb-20 pt-16 md:grid-cols-[1.02fr_.98fr] md:px-8 md:pb-24 md:pt-24">
        <div className={`transition duration-700 ${ready ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"}`}>
          <p className="text-xs font-semibold uppercase tracking-[.2em] text-[var(--lr-color-secondary)]">AI-assisted · human-approved</p>
          <h1 className="mt-5 max-w-2xl text-5xl font-semibold leading-[1.03] tracking-[-.045em] md:text-6xl">從素材到成片，<span className="text-[var(--lr-color-primary)]">每一步都由你確認。</span></h1>
          <p className="mt-6 max-w-xl text-base leading-7 text-[var(--lr-color-text-secondary)]">VantaCut 把粗剪、重新取景與多平台交付整理成一個清楚的工作流。AI 處理重複工作，你保留創作判斷。</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/studio" className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-5 py-3 text-sm font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">開始剪輯</Link>
            <Link href="/studio?mode=demo" className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border-strong)] bg-[var(--lr-color-surface)] px-5 py-3 text-sm font-semibold text-[var(--lr-color-text-primary)] hover:bg-[var(--lr-color-surface-raised)]">查看互動範例</Link>
          </div>
          <p className="mt-4 text-xs text-[var(--lr-color-text-muted)]">本機快取優先 · AI 修改可復原 · 不在網址列傳送登入憑證</p>
        </div>

        <div className={`transition delay-150 duration-700 ${ready ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"}`}>
          <div className="overflow-hidden rounded-[var(--lr-radius-lg)] border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] shadow-[var(--lr-shadow-md)]">
            <div className="flex h-11 items-center justify-between border-b border-[var(--lr-color-border)] px-4 text-xs"><span className="font-medium">旅行紀錄 / Iceland</span><span className="flex items-center gap-2 text-[var(--lr-color-success)]"><span className="h-1.5 w-1.5 rounded-full bg-current" />提案已準備</span></div>
            <div className="grid gap-3 p-3 sm:grid-cols-[1fr_9rem]">
              <div className="aspect-video border border-[var(--lr-color-border)] bg-[#111820] p-4"><div className="flex h-full flex-col justify-between border border-white/15 bg-[linear-gradient(150deg,rgba(123,167,255,.18),transparent_60%)] p-3"><span className="w-fit bg-black/50 px-2 py-1 text-[10px] text-[var(--lr-color-text-secondary)]">9:16 · 主體已置中</span><div className="border-l-2 border-[var(--lr-color-accent)] bg-black/55 px-3 py-2 text-sm font-semibold">冰島自駕，最值得做的一件事</div></div></div>
              <aside className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface-raised)] p-3 text-xs"><p className="font-semibold">提案摘要</p><dl className="mt-4 space-y-3 text-[var(--lr-color-text-muted)]"><div><dt>保留片段</dt><dd className="mt-1 font-mono text-[var(--lr-color-text-primary)]">00:42</dd></div><div><dt>靜音標記</dt><dd className="mt-1 font-mono text-[var(--lr-color-warning)]">3 段</dd></div><div><dt>版本</dt><dd className="mt-1 text-[var(--lr-color-text-primary)]">直式社群</dd></div></dl></aside>
            </div>
            <div className="border-t border-[var(--lr-color-border)] p-3"><div className="grid grid-cols-12 gap-1"><div className="col-span-4 h-8 bg-[var(--lr-color-primary-soft)] ring-1 ring-inset ring-[var(--lr-color-primary)]/40" /><div className="col-span-2 h-8 bg-[var(--lr-color-accent-soft)] ring-1 ring-inset ring-[var(--lr-color-accent)]/50" /><div className="col-span-6 h-8 bg-[var(--lr-color-primary-soft)] ring-1 ring-inset ring-[var(--lr-color-primary)]/40" /></div><div className="mt-2 flex justify-between text-[11px] text-[var(--lr-color-text-muted)]"><span>00:00</span><span>先預覽，再採納</span><span>00:58</span></div></div>
          </div>
        </div>
      </section>

      <section id="workflow" className="relative z-10 border-y border-[var(--lr-color-border)] bg-[var(--lr-color-surface)]">
        <div className="mx-auto max-w-7xl px-5 py-14 md:px-8">
          <div className="flex flex-col justify-between gap-3 md:flex-row md:items-end"><div><p className="text-xs font-semibold uppercase tracking-[.2em] text-[var(--lr-color-secondary)]">工作流程</p><h2 className="mt-2 text-2xl font-semibold">清楚、可檢查、可撤回</h2></div><p className="max-w-lg text-sm leading-6 text-[var(--lr-color-text-secondary)]">專業工具在需要時展開；主要操作始終留在同一條清楚路徑上。</p></div>
          <ol className="mt-8 grid border border-[var(--lr-color-border)] md:grid-cols-3">{workflow.map((step, index) => <li key={step} className="border-b border-[var(--lr-color-border)] p-5 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0"><span className="font-mono text-xs text-[var(--lr-color-primary)]">0{index + 1}</span><h3 className="mt-8 text-lg font-semibold">{step}</h3><p className="mt-2 text-sm leading-6 text-[var(--lr-color-text-muted)]">{index === 0 ? "從手機、相機或電腦匯入，素材立即可瀏覽。" : index === 1 ? "所有調整先以提案呈現，不覆寫原始素材。" : "建立各平台比例，集中下載或接續發佈流程。"}</p></li>)}</ol>
        </div>
      </section>

      <section id="features" className="relative z-10 mx-auto max-w-7xl px-5 py-20 md:px-8"><div className="grid gap-px overflow-hidden rounded-[var(--lr-radius-lg)] border border-[var(--lr-color-border)] bg-[var(--lr-color-border)] md:grid-cols-3">{capabilities.map(([number, title, text]) => <article key={title} className="bg-[var(--lr-color-surface)] p-6"><span className="font-mono text-xs text-[var(--lr-color-accent)]">{number}</span><h2 className="mt-12 text-lg font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-[var(--lr-color-text-secondary)]">{text}</p></article>)}</div></section>

      <section id="trust" className="relative z-10 mx-auto max-w-7xl px-5 pb-20 md:px-8"><div className="flex flex-col justify-between gap-8 border-t border-[var(--lr-color-border)] pt-10 md:flex-row md:items-center"><div><p className="text-xs font-semibold uppercase tracking-[.2em] text-[var(--lr-color-secondary)]">資料控制</p><h2 className="mt-2 text-2xl font-semibold">素材與決策都由你掌控</h2><p className="mt-3 max-w-2xl text-sm leading-6 text-[var(--lr-color-text-secondary)]">VantaCut 保存非破壞性設定，不覆寫原始影片。登入憑證由驗證後的伺服器身分處理，不放進查詢字串。</p></div><Link href="/studio" className="w-fit rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-5 py-3 text-sm font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">建立第一個專案</Link></div></section>

      <footer className="relative z-10 border-t border-[var(--lr-color-border)]"><div className="mx-auto flex max-w-7xl flex-col justify-between gap-4 px-5 py-7 text-xs text-[var(--lr-color-text-muted)] sm:flex-row sm:items-center md:px-8"><span>VantaCut · AI-assisted, human-approved editing.</span><Image src="/brand/lucirel-horizontal-dark.svg" width={108} height={28} alt="Lucirel" /></div></footer>
    </main>
  );
}
