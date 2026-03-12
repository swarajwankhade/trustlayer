from fastapi.testclient import TestClient


def test_admin_dashboard_ui_returns_200(client: TestClient) -> None:
    response = client.get("/admin")

    assert response.status_code == 200


def test_admin_dashboard_ui_includes_key_sections(client: TestClient) -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    html = response.text
    assert "Runtime Controls" in html
    assert "Active Policy" in html
    assert "Decision Metrics" in html
    assert "Exposure Metrics" in html
    assert "Recent Decisions" in html
    assert "Refresh Dashboard" in html
    assert "Apply Controls" in html
    assert "Simulation" in html
    assert "Run Simulation" in html
    assert "refund_amount_cents" in html
    assert "credit_amount_cents" in html
