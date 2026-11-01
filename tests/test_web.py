def test_web_assets_are_bundled_and_do_not_relax_api_auth(client):
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="uk">' in response.text
    assert 'src="/app.js"' in response.text
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200
    assert client.get("/mark.svg").status_code == 200
    response = client.get("/api/v1/projects/00000000-0000-4000-8000-000000000001/snapshot")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/does-not-exist").status_code == 404
    assert client.get("/../settings.py").status_code == 404
