import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

function loginErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Invalid username or password.";
    if (err.status === 429) return "Too many login attempts. Wait a minute and try again.";
    if (err.status === 422) return "Username and password are required.";
    if (err.status === 0) return "Cannot reach the Central API.";
    return `Login failed (HTTP ${err.status}).`;
  }
  return "Login failed.";
}

export function LoginPage() {
  const { session, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const expired = Boolean((location.state as { expired?: boolean } | null)?.expired);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session) return <Navigate to="/" replace />;

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!username || !password) {
      setError("Username and password are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      setPassword("");
      navigate("/", { replace: true });
    } catch (err) {
      setError(loginErrorText(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={onSubmit} noValidate aria-labelledby="login-title">
        <div className="brand brand-login">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <h1 id="login-title" className="brand-name">
              Orchid Control Platform
            </h1>
            <span className="brand-sub">Sign in to the greenhouse dashboard</span>
          </div>
        </div>
        {expired && !error ? (
          <div className="banner banner-stale" role="status" data-level="stale">
            <strong>SESSION</strong>
            <span>Your session ended. Please sign in again.</span>
          </div>
        ) : null}
        <label className="field">
          <span className="field-label">Username</span>
          <input
            data-testid="login-username"
            type="text"
            autoComplete="username"
            maxLength={64}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            data-testid="login-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? (
          <div className="banner banner-critical" role="alert" data-testid="login-error" data-level="critical">
            <strong>ERROR</strong>
            <span>{error}</span>
          </div>
        ) : null}
        <button type="submit" className="btn btn-primary btn-block" data-testid="login-submit" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
        <p className="muted small">Roles: viewer (read-only) and operator (can send commands).</p>
      </form>
    </div>
  );
}
