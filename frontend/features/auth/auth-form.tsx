"use client";

import Image from "next/image";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useAuthStore } from "@/lib/auth/auth-store";

type Mode = "login" | "register";

export function AuthForm() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const login = useAuthStore((state) => state.login);
  const register = useAuthStore((state) => state.register);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setPending(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
      // On success the auth store's status flips to "authenticated" and
      // AuthGate swaps this form out for the real app — no navigation call
      // needed here.
    } catch {
      // Deliberately generic, and deliberately not more specific than the
      // backend's own error detail (already surfaced into the store's
      // `error` field) — do not tell an attacker whether an email exists.
      setFormError(mode === "login" ? "登入失敗，請確認帳號密碼。" : "註冊失敗，請確認資訊後再試一次。");
    } finally {
      setPending(false);
    }
  };

  const switchMode = () => {
    setMode((current) => (current === "login" ? "register" : "login"));
    setFormError(null);
  };

  return (
    <main className="grid min-h-screen bg-[var(--lr-color-background)] text-[var(--lr-color-text-primary)] lg:grid-cols-[1fr_30rem]">
      <section className="relative hidden overflow-hidden border-r border-[var(--lr-color-border)] p-10 lg:flex lg:flex-col lg:justify-between">
        <div aria-hidden className="absolute inset-0 bg-[radial-gradient(circle_at_25%_15%,rgba(123,167,255,.15),transparent_42%)]" />
        <Link href="/" className="relative flex items-center gap-3" aria-label="返回 VantaCut 首頁">
          <Image src="/brand/lucirel-symbol-color-dark.svg" width={30} height={30} alt="" priority />
          <span className="text-sm font-semibold">VantaCut</span>
          <span className="border-l border-[var(--lr-color-border)] pl-3 text-[11px] uppercase tracking-[.18em] text-[var(--lr-color-text-muted)]">by Lucirel</span>
        </Link>
        <div className="relative max-w-2xl pb-10">
          <p className="text-xs font-semibold uppercase tracking-[.2em] text-[var(--lr-color-secondary)]">你的工作區</p>
          <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-[-.035em]">回到素材、時間軸與<br />尚未完成的想法。</h1>
          <p className="mt-5 max-w-lg text-sm leading-7 text-[var(--lr-color-text-secondary)]">登入後驗證身分，再載入屬於你的專案。VantaCut 不接受由瀏覽器自行聲明的使用者身分。</p>
        </div>
        <Image className="relative opacity-80" src="/brand/lucirel-horizontal-dark.svg" width={112} height={30} alt="Lucirel" />
      </section>

      <section className="flex min-h-screen items-center justify-center px-5 py-12 lg:px-12">
        <div className="w-full max-w-sm">
          <Link href="/" className="mb-10 flex items-center gap-3 lg:hidden"><Image src="/brand/lucirel-symbol-color-dark.svg" width={28} height={28} alt="" /><span className="text-sm font-semibold">VantaCut</span></Link>
          <p className="text-xs font-semibold uppercase tracking-[.2em] text-[var(--lr-color-secondary)]">安全工作階段</p>
          <h2 className="mt-3 text-2xl font-semibold tracking-[-.02em]">{mode === "login" ? "登入你的帳號" : "建立新帳號"}</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--lr-color-text-secondary)]">{mode === "login" ? "登入後繼續你的剪輯工作區。" : "建立帳號後會直接進入工作室。"}</p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-5" noValidate>
          <div>
            <label htmlFor="auth-email" className="mb-2 block text-xs font-medium text-[var(--lr-color-text-secondary)]">
              電子郵件
            </label>
            <input
              id="auth-email"
              name="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] px-3 py-2.5 text-sm text-[var(--lr-color-text-primary)] outline-none hover:border-[var(--lr-color-border-strong)] focus:border-[var(--lr-color-primary)]"
            />
          </div>
          <div>
            <label htmlFor="auth-password" className="mb-2 block text-xs font-medium text-[var(--lr-color-text-secondary)]">
              密碼
            </label>
            <input
              id="auth-password"
              name="password"
              type="password"
              required
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] px-3 py-2.5 text-sm text-[var(--lr-color-text-primary)] outline-none hover:border-[var(--lr-color-border-strong)] focus:border-[var(--lr-color-primary)]"
            />
          </div>

          {formError && (
            <p role="alert" className="border-l-2 border-[var(--lr-color-error)] pl-3 text-xs text-[var(--lr-color-error)]">
              {formError}
            </p>
          )}

          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-4 py-2.5 text-sm font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? "處理中…" : mode === "login" ? "登入" : "建立帳號並登入"}
          </button>
        </form>

        <button
          type="button"
          onClick={switchMode}
          className="mt-6 w-full text-center text-xs text-[var(--lr-color-text-muted)] underline decoration-dotted underline-offset-4 hover:text-[var(--lr-color-text-primary)]"
        >
          {mode === "login" ? "還沒有帳號？建立一個" : "已經有帳號了？前往登入"}
        </button>
        <p className="mt-10 border-t border-[var(--lr-color-border)] pt-5 text-xs leading-5 text-[var(--lr-color-text-muted)]">登入憑證只透過授權標頭傳送；工作階段失效時會要求重新登入。</p>
        </div>
      </section>
    </main>
  );
}
