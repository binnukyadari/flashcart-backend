from fastapi.testclient import TestClient

from flashcart.api import create_app
from flashcart.config import Settings


def test_storefront_has_no_server_key_and_strict_asset_policy(service):
    key = "private-training-key-" + "x" * 40
    with TestClient(create_app(Settings(api_key=key), service)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Training catalog" in response.text and "Place training order" in response.text
        assert key not in response.text
        policy = response.headers["content-security-policy"]
        assert "script-src 'sha256-" in policy and "frame-ancestors 'none'" in policy
        assert "unsafe-inline" not in policy and response.headers["cache-control"] == "no-store"
        assert client.get("/v1/products").status_code == 401
        assert client.get("/v1/products", headers={"X-API-Key": key}).status_code == 200
