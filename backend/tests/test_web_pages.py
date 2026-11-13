"""Direct links render the appropriate browser shell without embedding cargo data."""

from fastapi.testclient import TestClient

from app.main import app


def test_home_and_product_routes_are_separate():
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert 'id="freight-video"' in home.text
        assert 'id="operations"' not in home.text
        assert "/static/home.js" in home.text
        for path in ("/app", "/app/shipments", "/app/map", "/app/risks", "/app/documents", "/app/analytics"):
            page = client.get(path)
            assert page.status_code == 200, path
            assert page.headers["content-type"].startswith("text/html")
            assert "/static/workspace.js" in page.text
            assert 'id="freight-video"' not in page.text
            assert 'id="operations"' not in page.text
            assert "<!-- DISPATCHER -->" not in page.text
        assert client.get("/app/not-a-page").status_code == 404


def test_direct_shipment_link_contains_dispatcher_only_once():
    with TestClient(app) as client:
        page = client.get("/app/shipments/SHP-1042?tab=documents")
        assert page.status_code == 200
        assert page.text.count('id="operations"') == 1
        assert page.text.count('id="corridor-panel"') == 1
        assert page.text.count('id="create-dialog"') == 1
        assert page.text.count('/static/command.js') == 1
        assert 'id="freight-video"' not in page.text
        assert 'id="hero-map"' not in page.text
        # Routing strings are never copied into the HTML template.
        suspicious = client.get('/app/shipments/%3Cscript%3Ealert(1)%3Cscript%3E')
        assert '<script>alert(1)<script>' not in suspicious.text
