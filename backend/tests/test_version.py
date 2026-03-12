from fastapi.testclient import TestClient


def test_version_endpoint_returns_expected_structure(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "trustlayer"
    assert isinstance(payload["version"], str)
    assert len(payload["version"]) > 0


def test_version_header_present_on_responses(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert "X-TrustLayer-Version" in response.headers
    assert len(response.headers["X-TrustLayer-Version"]) > 0
