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
    assert "Last refreshed" in html
    assert "Apply Controls" in html
    assert "Simulation" in html
    assert "Run Simulation" in html
    assert "refund_amount_cents" in html
    assert "credit_amount_cents" in html
    assert "Policy Editor" in html
    assert "Validate Policy" in html
    assert "Create Policy" in html
    assert "Activate Policy" in html
    assert "Demo Helpers" in html
    assert "Seed Demo Policy" in html
    assert "Generate Demo Events" in html
    assert "Reset Demo Data" in html
    assert "Policies" in html
    assert "View Rules" in html
    assert "Activate" in html
    assert "Compare Policies" in html
    assert "Policy Diff" in html
    assert "View / Replay" in html
    assert "Decision Detail" in html
    assert "Replay Result" in html
    assert "No decision selected yet." in html
    assert "Apply Filters" in html
    assert "Clear Filters" in html
    assert "action_type" in html
    assert "decision" in html
    assert "request_id" in html
    assert "Export Decisions" in html
    assert "Download JSON" in html
    assert "Load More" in html
