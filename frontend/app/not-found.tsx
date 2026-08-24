import Link from "next/link";

export default function NotFound() {
  return <main className="grid min-h-screen place-items-center bg-[var(--lr-color-background)] p-6 text-center text-[var(--lr-color-text-primary)]"><div><p className="font-mono text-sm text-[var(--lr-color-accent)]">404</p><h1 className="mt-3 text-3xl font-semibold">這個鏡頭不在時間軸上。</h1><p className="mt-3 text-sm text-[var(--lr-color-text-secondary)]">回到首頁，從你的素材重新開始。</p><Link href="/" className="mt-6 inline-block rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-4 py-2 text-sm font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">回到首頁</Link></div></main>;
}
