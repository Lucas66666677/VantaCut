import Link from "next/link";

export default function NotFound() {
  return <main className="grid min-h-screen place-items-center bg-zinc-950 p-6 text-center text-zinc-100"><div><p className="text-sm text-cyan-200">404</p><h1 className="mt-2 text-3xl font-semibold">這個鏡頭不在時間軸上。</h1><p className="mt-3 text-sm text-zinc-400">回到首頁，從你的素材重新開始。</p><Link href="/" className="mt-6 inline-block rounded-xl bg-cyan-200 px-4 py-2 text-sm font-semibold text-zinc-950">回到首頁</Link></div></main>;
}
