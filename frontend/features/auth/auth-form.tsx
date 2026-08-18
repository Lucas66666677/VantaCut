"use client";

import { useState, type FormEvent } from "react";

import { useAuthStore } from "@/lib/auth/auth-store";

type Mode = "login" | "register";

/**
 * Minimum functional sign-in/create-account screen. One page, toggled
 * between the two modes, rather than separate routes — this app's routing
 * (app/) has no auth routes at all yet and adding two new pages plus
 * navigation between them for a first pass would be more surface area than
 * this foundation needs. Reuses the existing dark/cyan visual language from
 * features/marketing/marketing-home.tsx rather than introducing new styling.
 */
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
    <main className="grid min-h-screen place-items-center bg-[#09090f] px-4 text-zinc-100">
      <div className="w-full max-w-sm rounded-3xl border border-white/10 bg-zinc-900/70 p-8 shadow-2xl backdrop-blur">
        <div className="mb-6 flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-cyan-200 to-violet-400 text-zinc-950 shadow-[0_0_24px_rgba(34,211,238,.35)]">
            V
          </span>
          VantaCut
        </div>

        <h1 className="text-lg font-semibold text-white">{mode === "login" ? "登入你的帳號" : "建立新帳號"}</h1>
        <p className="mt-1 text-sm text-zinc-400">
          {mode === "login" ? "登入後即可繼續你的剪輯工作區。" : "建立帳號後會直接登入，進入工作室。"}
        </p>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
          <div>
            <label htmlFor="auth-email" className="mb-1 block text-xs font-medium text-zinc-400">
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
              className="w-full rounded-xl border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-cyan-300"
            />
          </div>
          <div>
            <label htmlFor="auth-password" className="mb-1 block text-xs font-medium text-zinc-400">
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
              className="w-full rounded-xl border border-zinc-700 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-cyan-300"
            />
          </div>

          {formError && (
            <p role="alert" className="text-xs text-rose-300">
              {formError}
            </p>
          )}

          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-xl bg-white px-4 py-2.5 text-sm font-bold text-zinc-950 transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? "處理中…" : mode === "login" ? "登入" : "建立帳號並登入"}
          </button>
        </form>

        <button
          type="button"
          onClick={switchMode}
          className="mt-5 w-full text-center text-xs text-zinc-400 underline decoration-dotted underline-offset-4 hover:text-zinc-200"
        >
          {mode === "login" ? "還沒有帳號？建立一個" : "已經有帳號了？前往登入"}
        </button>
      </div>
    </main>
  );
}
