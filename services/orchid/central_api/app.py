"""Central API (DMZ). Read views + operator commands. No secrets in responses or logs."""

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from orchid.central_api.auth import LoginRateLimiter, Principal, TokenService, UserStore
from orchid.central_api.publisher import PublishError
from orchid.central_api.store import DataUnavailable, Store
from orchid.core import topics
from orchid.core.config import Config, load_config
from orchid.core.timeutil import iso, utcnow

TEXT = r"^[A-Za-z0-9_.:\- ]{1,64}$"


class Publisher(Protocol):
    connected: bool

    def publish_command(self, topic: str, payload: bytes, ttl: int) -> int: ...
    def audit_event(self, event_type: str, **details: Any) -> None: ...


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class GroupCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal["fan_group", "dehumidifier_group"]
    desired_state: Literal["ON", "OFF"]
    reason: str = Field(pattern=TEXT)
    ttl_seconds: int | None = Field(default=None, gt=0, le=300)


class ModeCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["AUTO", "MANUAL_OVERRIDE", "MAINTENANCE"]
    reason: str = Field(pattern=TEXT)
    ttl_seconds: int | None = Field(default=None, gt=0, le=86400)


def create_app(cfg: Config, *, db_path: Path, users: dict[str, dict[str, str]], jwt_secret: str,
               publisher: Publisher) -> FastAPI:
    app = FastAPI(title="Orchid Central API", docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(cfg, db_path)
    users_store = UserStore(users)
    tokens = TokenService(jwt_secret, cfg.platform.web.jwt_ttl_seconds, cfg.platform.web.jwt_algorithm)
    limiter = LoginRateLimiter(cfg.platform.web.login_rate_limit_per_minute)
    bearer = HTTPBearer(auto_error=False)

    def client_ip(request: Request) -> str:
        # X-Real-IP is set by the reverse proxy (the only ingress path); fallback to socket peer.
        return (request.headers.get("x-real-ip") or (request.client.host if request.client else "?"))[:64]

    def current(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
        principal = tokens.verify(creds.credentials) if creds and creds.scheme.lower() == "bearer" else None
        if principal is None:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        return principal

    def operator(request: Request, principal: Annotated[Principal, Depends(current)]) -> Principal:
        if principal.role != "operator":
            publisher.audit_event("AUTHZ_DENIED", username=principal.username, role=principal.role,
                                  path=request.url.path[:64])
            raise HTTPException(403, "operator role required")
        return principal

    def known_site(site_id: str) -> str:
        if site_id not in cfg.sites:
            raise HTTPException(404, "unknown site")
        return site_id

    @app.exception_handler(DataUnavailable)
    async def _unavailable(_: Request, exc: DataUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "historian data unavailable"})

    @app.exception_handler(PublishError)
    async def _publish_error(_: Request, exc: PublishError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.middleware("http")
    async def _no_store(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mqtt_connected": bool(publisher.connected)}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request) -> dict[str, Any]:
        ip = client_ip(request)
        if not limiter.allow(f"ip:{ip}", f"user:{ip}:{body.username}"):
            raise HTTPException(429, "too many login attempts")
        principal = users_store.authenticate(body.username, body.password)
        if principal is None:
            publisher.audit_event("LOGIN_FAILURE", username=body.username[:64], client=ip)
            raise HTTPException(401, "invalid credentials")
        publisher.audit_event("LOGIN_SUCCESS", username=principal.username, role=principal.role, client=ip)
        return {"access_token": tokens.issue(principal), "token_type": "bearer", "role": principal.role,
                "username": principal.username, "expires_in": tokens.ttl}

    @app.get("/api/me")
    def me(principal: Annotated[Principal, Depends(current)]) -> dict[str, str]:
        return {"username": principal.username, "role": principal.role}

    @app.get("/api/overview")
    def overview(_: Annotated[Principal, Depends(current)]) -> dict[str, Any]:
        return store.overview(utcnow())

    @app.get("/api/sites/{site_id}")
    def site_detail(site_id: str, _: Annotated[Principal, Depends(current)]) -> dict[str, Any]:
        return store.site_detail(known_site(site_id), utcnow())

    @app.get("/api/system")
    def system(_: Annotated[Principal, Depends(current)]) -> dict[str, Any]:
        out = store.system(utcnow())
        out["central_api"] = {"mqtt_connected": bool(publisher.connected)}
        return out

    @app.get("/api/events")
    def events(_: Annotated[Principal, Depends(current)], site: str | None = None, severity: str | None = None,
               kind: str | None = None, limit: int = 50) -> dict[str, Any]:
        if site is not None:
            known_site(site) if site != "central" else None
        kinds = (kind,) if kind in ("alert", "event", "security", "audit") else None
        sev = severity if severity in ("INFO", "NOTICE", "WARNING", "CRITICAL", "SECURITY") else None
        return {"events": store.events(site=site, kinds=kinds, severity=sev, limit=limit)}

    def _send(site_id: str, group: str, body: dict[str, Any], ttl: int, principal: Principal,
              audit_type: str) -> dict[str, Any]:
        now = utcnow()
        cid = str(uuid.uuid4())
        payload = {"command_id": cid, "issued_at": iso(now), "expires_at": iso(now + timedelta(seconds=ttl)),
                   "operator": principal.username, **body}
        publisher.publish_command(topics.group_command(site_id, group),
                                  json.dumps(payload, separators=(",", ":")).encode(), ttl)
        publisher.audit_event(audit_type, username=principal.username, site=site_id, group=group,
                              command_id=cid, **{k: v for k, v in body.items() if k != "reason"},
                              reason=body.get("reason"))
        return {"command_id": cid, "expires_at": payload["expires_at"], "status": "SENT"}

    @app.post("/api/sites/{site_id}/commands", status_code=202)
    def command(site_id: str, body: GroupCommandIn,
                principal: Annotated[Principal, Depends(operator)]) -> dict[str, Any]:
        known_site(site_id)
        ttl = body.ttl_seconds or cfg.platform.command.default_ttl_seconds
        return _send(site_id, body.group, {"desired_state": body.desired_state, "reason": body.reason}, ttl,
                     principal, "OPERATOR_COMMAND")

    @app.post("/api/sites/{site_id}/mode", status_code=202)
    def mode(site_id: str, body: ModeCommandIn, principal: Annotated[Principal, Depends(operator)]) -> dict[str, Any]:
        known_site(site_id)
        extra: dict[str, Any] = {"mode": body.mode, "reason": body.reason}
        if body.ttl_seconds:
            extra["ttl_seconds"] = body.ttl_seconds
        return _send(site_id, "mode", extra, cfg.platform.command.default_ttl_seconds, principal,
                     "OPERATOR_MODE_CHANGE")

    return app


def build_production_app() -> FastAPI:
    from orchid.central_api.publisher import MqttPublisher
    from orchid.core.mqttc import read_secret

    cfg = load_config()
    users = json.loads(read_secret("web_users.json"))
    publisher = MqttPublisher(cfg)
    publisher.start()
    return create_app(cfg, db_path=Path(os.environ.get("ORCHID_DB_PATH", "/data/historian.db")), users=users,
                      jwt_secret=read_secret("jwt_secret"), publisher=publisher)
