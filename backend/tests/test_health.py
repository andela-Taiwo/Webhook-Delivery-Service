from httpx import ASGITransport, AsyncClient

from webhook_service.main import app, create_app


async def test_probes_are_mounted_at_the_root_and_not_under_the_api_prefix() -> None:
    """The probes must answer at the root, and only at the root.

    Asserted by issuing real requests against a freshly built app rather than
    by inspecting `app.routes`: since FastAPI 0.141 `include_router` stores a
    lazy `_IncludedRouter` and included paths no longer appear in that list,
    so introspection there proves nothing about what the app actually serves.

    Probes stay versionless on purpose — kubelets and load balancers should
    not need reconfiguring when the API version changes.
    """
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/healthz")).status_code == 200
        # 503 here is fine and expected without infrastructure; what matters
        # is that the route exists rather than 404s.
        assert (await client.get("/readyz")).status_code in (200, 503)
        assert (await client.get("/api/v1/healthz")).status_code == 404
        assert (await client.get("/api/v1/readyz")).status_code == 404


async def test_liveness() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_dependency_state() -> None:
    """Readiness answers 200 or 503 depending on the dependencies it checks.

    This test runs without infrastructure, so 503 is the expected and correct
    answer here — the point is that the endpoint reports per-dependency
    detail either way. The 200/503 mapping itself is pinned in
    test_health_checks.py, and the real-infrastructure path in
    test_health_integration.py.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code in (200, 503)
    body = response.json()
    assert body["status"] in ("ready", "not_ready")
    assert set(body["dependencies"]) == {"postgres", "redis", "kafka"}


async def test_tenant_header_echoed_in_request_id() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz", headers={"X-Request-ID": "test-123"})
    assert response.headers["X-Request-ID"] == "test-123"
