import { useState, type FormEvent } from "react";
import { login } from "../lib/api";

/**
 * Shown only when the server has logins on and nobody is signed in.
 *
 * The password goes straight into the request and is not kept in state after a
 * successful sign-in: the session cookie is HttpOnly, so nothing in the page
 * ever holds a credential that lasts.
 */
export default function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    const result = await login(username.trim(), password);
    setBusy(false);
    if (result.ok) {
      setPassword("");
      onSignedIn();
      return;
    }
    setMessage(
      result.status === 429
        ? "Too many failed attempts. Wait a few minutes and try again."
        : (result.detail ?? `Sign-in failed (${result.status}).`),
    );
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit} aria-label="Sign in">
        <h1>PEAT</h1>
        <label className="sheet-row">
          <span className="sheet-label">Username</span>
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </label>
        <label className="sheet-row">
          <span className="sheet-label">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {message && <p className="sheet-error">{message}</p>}
        <button className="primary" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
