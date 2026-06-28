// Auth API — fastapi-users routes under /api/auth.
// Login sets an httpOnly session cookie; the browser sends it on every
// same-origin request after that. `credentials: "include"` is explicit belt-and-
// braces (same-origin already sends cookies by default via nginx / the Vite proxy).

export interface AuthUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
}

// Current user, or null when there's no valid session (401).
export async function fetchMe(): Promise<AuthUser | null> {
  const res = await fetch("/api/auth/me", { credentials: "include" });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(`Failed to load current user: ${res.status}`);
  return (await res.json()) as AuthUser;
}

// fastapi-users login is an OAuth2 password form (username + password).
// On success it returns 204 with a Set-Cookie; 400 means bad credentials.
export async function login(email: string, password: string): Promise<void> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    body: new URLSearchParams({ username: email, password }),
  });
  if (res.status === 400) throw new Error("Invalid email or password.");
  if (!res.ok && res.status !== 204) {
    throw new Error(`Login failed (${res.status}).`);
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}
