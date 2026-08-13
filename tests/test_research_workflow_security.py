from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from src.api.workflow_security import (
    CSRF_COOKIE_NAME,
    CsrfSessionStore,
    ResearchBoundaryMiddleware,
    ResearchSecurityConfig,
)


def _app(config=None):
    app = FastAPI()
    app.add_middleware(ResearchBoundaryMiddleware, config=config)
    @app.get("/api/v2/research/queue")
    def queue():
        return {"status": "available"}
    @app.post("/api/v2/research/queue")
    def add():
        return {"created": True}
    @app.get("/api/v2/research/csrf-token")
    def csrf(request: Request, response: Response):
        now = request.state.research_request_received_at
        session, token, expires = request.state.research_csrf_sessions.issue(now)
        response.set_cookie(CSRF_COOKIE_NAME, session, httponly=True, samesite="strict",
                            path="/api/v2/research")
        return {"token": token, "expires_at": expires.isoformat()}
    return app


def _valid(monkeypatch):
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")
    monkeypatch.delenv("RESEARCH_ALLOWED_DEV_ORIGINS", raising=False)
    return ResearchSecurityConfig.from_environment()


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


def test_research_configuration_fails_closed(monkeypatch):
    for value in (None, "bad", "https://example.com:443", "http://localhost", "http://*:8000"):
        if value is None:
            monkeypatch.delenv("RESEARCH_APPLICATION_ORIGIN", raising=False)
        else:
            monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", value)
        with _client(_app(ResearchSecurityConfig.from_environment())) as client:
            response = client.get("/api/v2/research/queue", headers={"host": "127.0.0.1:8000"})
            assert response.status_code == 503
            assert response.json()["detail"] == "research_security_configuration_invalid"


def test_research_get_origin_host_and_loopback_contract(monkeypatch):
    config = _valid(monkeypatch)
    with _client(_app(config)) as client:
        assert client.get("/api/v2/research/queue").status_code == 200
        assert client.get("/api/v2/research/queue", headers={"origin": "http://evil.test:8000"}).status_code == 403
        assert client.get("/api/v2/research/queue", headers={"host": "localhost:9999"}).status_code == 403
    remote = TestClient(_app(config), base_url="http://127.0.0.1:8000", client=("192.0.2.1", 50000))
    with remote:
        assert remote.get("/api/v2/research/queue").status_code == 403


def test_research_write_requires_flag_origin_json_and_csrf(monkeypatch):
    config = _valid(monkeypatch)
    with _client(_app(config)) as client:
        assert client.post("/api/v2/research/queue", json={}).status_code == 403
        headers = {"origin": "http://127.0.0.1:8000"}
        assert client.post("/api/v2/research/queue", json={}, headers=headers).status_code == 503
        monkeypatch.setenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "true")
        bootstrap = client.get("/api/v2/research/csrf-token", headers=headers)
        token = bootstrap.json()["token"]
        assert client.post("/api/v2/research/queue", json={}, headers={**headers, "x-csrf-token": token}).status_code == 200
        assert client.post("/api/v2/research/queue", content=b"{}", headers={**headers, "x-csrf-token": token, "content-type": "text/plain"}).status_code == 415
        assert client.post("/api/v2/research/queue", content=b"x" * 16385, headers={**headers, "x-csrf-token": token, "content-type": "application/json"}).status_code == 413


def test_csrf_expiry_is_exact_and_capacity_is_bounded():
    store = CsrfSessionStore()
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    session, token, expires = store.issue(now)
    assert store.validate(session, token, expires - timedelta(microseconds=1)) is None
    assert store.validate(session, token, expires) == "csrf_session_expired"
