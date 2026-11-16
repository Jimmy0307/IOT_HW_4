import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { login as apiLogin, setUnauthorizedHandler } from "../api/client";
import { clearSession, getSession, type Session } from "./session";

interface AuthValue {
  session: Session | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(() => getSession());
  const navigate = useNavigate();

  const logout = useCallback(() => {
    clearSession();
    setSessionState(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearSession();
      setSessionState(null);
      navigate("/login", { replace: true, state: { expired: true } });
    });
    return () => setUnauthorizedHandler(() => undefined);
  }, [navigate]);

  // Proactively drop the session when the JWT lifetime elapses.
  useEffect(() => {
    if (!session) return undefined;
    const ms = Math.max(0, session.expiresAt - Date.now());
    const timer = setTimeout(() => {
      clearSession();
      setSessionState(null);
      navigate("/login", { replace: true, state: { expired: true } });
    }, ms);
    return () => clearTimeout(timer);
  }, [session, navigate]);

  const login = useCallback(async (username: string, password: string) => {
    await apiLogin(username, password);
    setSessionState(getSession());
  }, []);

  const value = useMemo(() => ({ session, login, logout }), [session, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
