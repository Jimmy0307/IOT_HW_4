import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { session, logout } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <span className="brand-name">Orchid Control Platform</span>
            <span className="brand-sub">Secure IoT greenhouse monitoring</span>
          </div>
        </div>
        <nav className="nav" aria-label="Main">
          <NavLink to="/" end className="nav-link" data-testid="nav-overview">
            Overview
          </NavLink>
          <NavLink to="/system" className="nav-link" data-testid="nav-system">
            System &amp; Security
          </NavLink>
        </nav>
        <div className="user">
          {session ? (
            <span className="user-info" data-testid="user-info">
              <span className="user-name">{session.username}</span>
              <span className={`role role-${session.role}`} data-testid="user-role">
                {session.role}
              </span>
            </span>
          ) : null}
          <button type="button" className="btn btn-ghost" data-testid="nav-logout" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
