import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { useAuth } from "../hooks/useAuth";

// Auth gate around the whole app. Checks the session once on mount, then:
//   loading → splash, authed → the app (children), anon → the login form.
// Placed OUTSIDE <Bootstrap> in main.tsx so conversation bring-up (which now
// requires auth) only runs once we're logged in.
export function LoginGate({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status);
  const refresh = useAuth((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (status === "loading") return <Splash>checking session…</Splash>;
  if (status === "authed") return <>{children}</>;
  return <LoginForm />;
}

function Splash({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen items-center justify-center bg-paper text-sm text-ink/50">
      {children}
    </div>
  );
}

function LoginForm() {
  const login = useAuth((s) => s.login);
  const [email, setEmail] = useState("dev@flat-chat.dev");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-paper px-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm border border-paper-rule bg-paper p-8"
      >
        <header className="mb-7 border-b-2 border-red pb-4 text-center">
          <h1 className="font-sans text-[2rem] font-extrabold leading-none tracking-[-0.035em] text-ink">
            Flat<span className="px-1 text-red">·</span>Chat
          </h1>
          <span className="mt-2.5 inline-block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
            Sign in to continue
          </span>
        </header>

        <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
          Email
        </label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
          className="mb-4 w-full border border-paper-rule bg-white px-3 py-2 font-mono text-sm text-ink outline-none focus:border-red"
        />

        <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
          className="mb-5 w-full border border-paper-rule bg-white px-3 py-2 font-mono text-sm text-ink outline-none focus:border-red"
        />

        {error && (
          <p className="mb-4 font-mono text-[11px] text-red" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full bg-red px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.16em] text-white transition-colors hover:bg-red-deep disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
