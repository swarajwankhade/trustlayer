from collections import Counter
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ValidationError
from redis import Redis
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from app.actions.service import ActionAuthorizationInput, authorize_action, get_or_init_kill_switch
from app.api.dependencies import require_api_key
from app.api.schemas import (
    ActionDecisionResponse,
    CreditActionRequest,
    CreatePolicyRequest,
    DashboardActivePolicy,
    DashboardResponse,
    DashboardRuntimeControls,
    DecisionEventResponse,
    DecisionMetricsResponse,
    DecisionReplayResponse,
    ExposureMetricsResponse,
    KillSwitchResponse,
    KillSwitchUpdateRequest,
    PolicyResponse,
    RefundActionRequest,
    SimulationRequest,
    SimulationResponse,
    ValidatePolicyRequest,
    ValidatePolicyResponse,
    cents_to_decimal,
)
from app.config import get_settings
from app.db.session import get_db_session, get_engine
from app.exposure.store import ExposureStore, get_exposure_store
from app.models import DecisionEvent, Policy
from app.policies.engine import evaluate_action
from app.policies.schemas import ExposureContext, PolicyRules
from app.policies.service import ActivePolicy, load_active_policy

router = APIRouter()
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict[str, str]:
    return {
        "service": "trustlayer",
        "version": get_settings().service_version,
    }


@router.get("/ready")
def readiness() -> JSONResponse:
    postgres = "ok" if _postgres_ready() else "error"
    redis = "ok" if _redis_ready() else "error"
    is_ready = postgres == "ok" and redis == "ok"

    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if is_ready else "degraded",
            "postgres": postgres,
            "redis": redis,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard_ui() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TrustLayer Operator Dashboard</title>
    <style>
      :root {
        --bg: #f5f7fa;
        --surface: #ffffff;
        --border: #dbe2ea;
        --text: #1d2734;
        --muted: #5d6b7a;
        --good: #1f7a45;
        --warn: #b96d00;
        --bad: #b42318;
        --chip-bg: #eef3f8;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        padding: 24px;
        font-family: "Segoe UI", Tahoma, sans-serif;
        background: var(--bg);
        color: var(--text);
      }
      .container { max-width: 1200px; margin: 0 auto; }
      h1 { margin: 0 0 8px; font-size: 28px; }
      h2 { margin: 0 0 12px; font-size: 18px; }
      .subtitle { margin: 0 0 20px; color: var(--muted); }
      .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 14px;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 14px;
      }
      .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
      .input {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 14px;
        min-width: 240px;
      }
      .button {
        border: 1px solid var(--border);
        background: #f8fafc;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 14px;
        cursor: pointer;
      }
      .button:hover { background: #eef3f8; }
      .button-primary {
        background: #12467f;
        color: #ffffff;
        border-color: #12467f;
      }
      .button-primary:hover { background: #0e3764; }
      .banner {
        margin-top: 10px;
        padding: 10px;
        border-radius: 8px;
        border: 1px solid transparent;
        font-size: 14px;
      }
      .banner-ok {
        background: #eaf8ee;
        border-color: #b9e5c7;
        color: var(--good);
      }
      .banner-error {
        background: #fff2f0;
        border-color: #f6c6bf;
        color: var(--bad);
      }
      .hidden { display: none; }
      .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
      .chip {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        background: var(--chip-bg);
        color: #2b3a4a;
        font-size: 12px;
        border: 1px solid var(--border);
      }
      .chip-good { background: #eaf8ee; color: var(--good); border-color: #b9e5c7; }
      .chip-warn { background: #fff7ea; color: var(--warn); border-color: #f3ddb2; }
      .chip-bad { background: #fff2f0; color: var(--bad); border-color: #f6c6bf; }
      .metrics {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 10px;
      }
      .metric {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
      }
      .metric .label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
      .metric .value { font-size: 20px; font-weight: 600; }
      pre {
        margin: 0;
        background: #f7f9fc;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
        overflow-x: auto;
        font-size: 12px;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }
      th, td {
        padding: 8px;
        border-bottom: 1px solid #edf1f5;
        text-align: left;
        vertical-align: top;
      }
      th { color: var(--muted); font-weight: 600; }
      .muted { color: var(--muted); }
      .stack { display: grid; gap: 14px; margin-top: 14px; }
      .inline-controls { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>TrustLayer Operator Dashboard</h1>
      <p class="subtitle">Operational snapshot from <code>/v1/admin/dashboard</code> with runtime controls from <code>/v1/admin/killswitch</code>.</p>

      <section class="card">
        <div class="toolbar">
          <label>
            API Key
            <input id="apiKey" class="input" type="password" placeholder="X-API-Key" />
          </label>
          <button id="refreshBtn" class="button button-primary">Refresh Dashboard</button>
          <span class="muted">Stored in local browser localStorage for demo convenience.</span>
        </div>
        <div id="loadBanner" class="banner hidden"></div>
      </section>

      <div class="stack">
        <section class="card">
          <h2>Runtime Controls</h2>
          <div class="chips" id="runtimeChips"></div>
          <div id="runtimeText" class="muted">Waiting for data.</div>
          <div class="inline-controls">
            <label><input id="killEnabled" type="checkbox" /> Kill Switch Enabled</label>
            <label><input id="observeOnly" type="checkbox" /> Observe Only</label>
          </div>
          <div class="toolbar">
            <label>Reason <input id="reason" class="input" type="text" value="updated from /admin UI" /></label>
            <label>Updated By <input id="updatedBy" class="input" type="text" value="operator-ui" /></label>
            <button id="applyControlsBtn" class="button">Apply Controls</button>
          </div>
          <div id="controlBanner" class="banner hidden"></div>
        </section>

        <section class="card">
          <h2>Active Policy</h2>
          <div id="policyBadges" class="chips"></div>
          <div id="activePolicyState" class="muted">Waiting for data.</div>
          <pre id="activePolicyRules">{}</pre>
        </section>

        <section class="card">
          <h2>Decision Metrics</h2>
          <div id="decisionMetricsGrid" class="metrics"></div>
          <div class="grid" style="margin-top: 10px;">
            <div>
              <h3>By Action Type</h3>
              <pre id="byActionType">{}</pre>
            </div>
            <div>
              <h3>By Reason Code</h3>
              <pre id="byReasonCode">{}</pre>
            </div>
          </div>
        </section>

        <section class="card">
          <h2>Exposure Metrics</h2>
          <div id="exposureMetricsGrid" class="metrics"></div>
        </section>

        <section class="card">
          <h2>Recent Decisions</h2>
          <table>
            <thead>
              <tr>
                <th>timestamp</th>
                <th>action_type</th>
                <th>decision</th>
                <th>would_decision</th>
                <th>reason_codes</th>
              </tr>
            </thead>
            <tbody id="recentDecisionsBody">
              <tr><td colspan="5" class="muted">No data loaded yet.</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>

    <script>
      const API_KEY_STORAGE_KEY = "trustlayer_admin_api_key";
      const apiKeyInput = document.getElementById("apiKey");
      const refreshBtn = document.getElementById("refreshBtn");
      const applyControlsBtn = document.getElementById("applyControlsBtn");
      const loadBanner = document.getElementById("loadBanner");
      const controlBanner = document.getElementById("controlBanner");

      function showBanner(node, message, ok) {
        node.textContent = message;
        node.classList.remove("hidden", "banner-ok", "banner-error");
        node.classList.add(ok ? "banner-ok" : "banner-error");
      }

      function hideBanner(node) {
        node.classList.add("hidden");
        node.textContent = "";
        node.classList.remove("banner-ok", "banner-error");
      }

      function getHeaders() {
        const key = apiKeyInput.value.trim();
        if (!key) return { "Content-Type": "application/json" };
        return { "Content-Type": "application/json", "X-API-Key": key };
      }

      function chipClassForDecision(value) {
        if (value === "ALLOW") return "chip chip-good";
        if (value === "ESCALATE") return "chip chip-warn";
        if (value === "BLOCK") return "chip chip-bad";
        return "chip";
      }

      function renderMetricGrid(nodeId, metrics) {
        const container = document.getElementById(nodeId);
        container.innerHTML = "";
        for (const [label, value] of metrics) {
          const card = document.createElement("div");
          card.className = "metric";
          card.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
          container.appendChild(card);
        }
      }

      function renderRuntimeControls(runtime) {
        const chips = document.getElementById("runtimeChips");
        chips.innerHTML = "";
        const killChip = document.createElement("span");
        killChip.className = runtime.kill_switch_enabled ? "chip chip-bad" : "chip chip-good";
        killChip.textContent = runtime.kill_switch_enabled ? "Kill Switch: Enabled" : "Kill Switch: Disabled";
        chips.appendChild(killChip);

        const observeChip = document.createElement("span");
        observeChip.className = runtime.observe_only ? "chip chip-warn" : "chip";
        observeChip.textContent = runtime.observe_only ? "Observe Only: Enabled" : "Observe Only: Disabled";
        chips.appendChild(observeChip);

        document.getElementById("runtimeText").textContent =
          `Reason: ${runtime.reason || "n/a"} | Updated by: ${runtime.updated_by || "n/a"} | Updated at: ${runtime.updated_at || "n/a"}`;
      }

      function renderActivePolicy(policy) {
        const badges = document.getElementById("policyBadges");
        const state = document.getElementById("activePolicyState");
        const rules = document.getElementById("activePolicyRules");
        badges.innerHTML = "";

        if (!policy) {
          state.textContent = "No active policy found.";
          rules.textContent = "{}";
          return;
        }

        const statusChip = document.createElement("span");
        statusChip.className = policy.status === "ACTIVE" ? "chip chip-good" : "chip";
        statusChip.textContent = `Status: ${policy.status}`;
        badges.appendChild(statusChip);

        const nameChip = document.createElement("span");
        nameChip.className = "chip";
        nameChip.textContent = `Name: ${policy.name}`;
        badges.appendChild(nameChip);

        const versionChip = document.createElement("span");
        versionChip.className = "chip";
        versionChip.textContent = `Version: ${policy.version}`;
        badges.appendChild(versionChip);

        state.textContent = `Policy ID: ${policy.policy_id}`;
        rules.textContent = JSON.stringify(policy.rules_json || {}, null, 2);
      }

      function renderRecentDecisions(items) {
        const tbody = document.getElementById("recentDecisionsBody");
        tbody.innerHTML = "";
        if (!items.length) {
          tbody.innerHTML = '<tr><td colspan="5" class="muted">No recent decisions available.</td></tr>';
          return;
        }

        const sorted = [...items].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        for (const item of sorted) {
          const tr = document.createElement("tr");
          const reasonCodes = (item.reason_codes || []).join(", ") || "-";
          const decisionClass = chipClassForDecision(item.decision);
          const wouldDecisionClass = chipClassForDecision(item.would_decision);
          tr.innerHTML = `
            <td>${item.timestamp}</td>
            <td>${item.action_type}</td>
            <td><span class="${decisionClass}">${item.decision}</span></td>
            <td>${item.would_decision ? `<span class="${wouldDecisionClass}">${item.would_decision}</span>` : "-"}</td>
            <td>${reasonCodes}</td>
          `;
          tbody.appendChild(tr);
        }
      }

      function setLoadingState() {
        document.getElementById("runtimeText").textContent = "Loading runtime controls...";
        document.getElementById("activePolicyState").textContent = "Loading policy...";
        document.getElementById("activePolicyRules").textContent = "{}";
        document.getElementById("byActionType").textContent = "{}";
        document.getElementById("byReasonCode").textContent = "{}";
        renderMetricGrid("decisionMetricsGrid", [["total_decisions", "..."]]);
        renderMetricGrid("exposureMetricsGrid", [["financial_total_amount_cents", "..."]]);
        const tbody = document.getElementById("recentDecisionsBody");
        tbody.innerHTML = '<tr><td colspan="5" class="muted">Loading recent decisions...</td></tr>';
      }

      async function refreshDashboard() {
        hideBanner(controlBanner);
        hideBanner(loadBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(loadBanner, "API key is required to load dashboard data.", false);
          return;
        }

        localStorage.setItem(API_KEY_STORAGE_KEY, key);
        setLoadingState();
        const response = await fetch("/v1/admin/dashboard", { headers: getHeaders() });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and refresh."
            : `Failed to load dashboard: ${JSON.stringify(data)}`;
          showBanner(loadBanner, message, false);
          return;
        }
        showBanner(loadBanner, "Dashboard data loaded.", true);

        renderRuntimeControls(data.runtime_controls);
        renderActivePolicy(data.active_policy);
        renderMetricGrid("decisionMetricsGrid", [
          ["total_decisions", data.decision_metrics.total_decisions],
          ["allow_count", data.decision_metrics.allow_count],
          ["escalate_count", data.decision_metrics.escalate_count],
          ["block_count", data.decision_metrics.block_count],
          ["observe_only_count", data.decision_metrics.observe_only_count],
          ["would_block_count", data.decision_metrics.would_block_count],
          ["would_escalate_count", data.decision_metrics.would_escalate_count]
        ]);
        renderMetricGrid("exposureMetricsGrid", [
          ["date_bucket_utc", data.exposure_metrics.date_bucket_utc],
          ["refund_daily_total_amount_cents", data.exposure_metrics.refund_daily_total_amount_cents],
          ["credit_daily_total_amount_cents", data.exposure_metrics.credit_daily_total_amount_cents],
          ["financial_total_amount_cents", data.exposure_metrics.financial_total_amount_cents]
        ]);
        document.getElementById("byActionType").textContent = JSON.stringify(data.decision_metrics.counts_by_action_type || {}, null, 2);
        document.getElementById("byReasonCode").textContent = JSON.stringify(data.decision_metrics.counts_by_reason_code || {}, null, 2);
        renderRecentDecisions(data.recent_decisions || []);

        document.getElementById("killEnabled").checked = !!data.runtime_controls.kill_switch_enabled;
        document.getElementById("observeOnly").checked = !!data.runtime_controls.observe_only;
      }

      async function applyControls() {
        const payload = {
          enabled: document.getElementById("killEnabled").checked,
          observe_only: document.getElementById("observeOnly").checked,
          reason: document.getElementById("reason").value || "updated from /admin UI",
          updated_by: document.getElementById("updatedBy").value || "operator-ui",
        };

        const response = await fetch("/v1/admin/killswitch", {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
          showBanner(controlBanner, `Failed to update controls: ${JSON.stringify(data)}`, false);
          return;
        }
        showBanner(controlBanner, "Runtime controls updated successfully.", true);
        await refreshDashboard();
      }

      refreshBtn.addEventListener("click", refreshDashboard);
      applyControlsBtn.addEventListener("click", applyControls);

      const savedApiKey = localStorage.getItem(API_KEY_STORAGE_KEY);
      if (savedApiKey) {
        apiKeyInput.value = savedApiKey;
        refreshDashboard();
      }
    </script>
  </body>
</html>
        """
    )


@v1_router.post("/actions/refund", response_model=ActionDecisionResponse)
def create_refund_action(
    payload: RefundActionRequest,
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> ActionDecisionResponse:
    decision_event = authorize_action(
        action=ActionAuthorizationInput(
            action_type="refund",
            request_id=payload.request_id,
            user_id=payload.user_id,
            amount=cents_to_decimal(payload.refund_amount_cents),
            model_version=payload.model_version,
            payload_json=_serialize_payload(payload),
        ),
        db=db,
        exposure_store=exposure_store,
    )
    return _build_action_response(decision_event)


@v1_router.post("/actions/credit", response_model=ActionDecisionResponse)
def create_credit_action(
    payload: CreditActionRequest,
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> ActionDecisionResponse:
    decision_event = authorize_action(
        action=ActionAuthorizationInput(
            action_type="credit_adjustment",
            request_id=payload.request_id,
            user_id=payload.user_id,
            amount=cents_to_decimal(payload.credit_amount_cents),
            model_version=payload.model_version,
            payload_json=_serialize_payload(payload),
        ),
        db=db,
        exposure_store=exposure_store,
    )
    return _build_action_response(decision_event)


@v1_router.get("/admin/policies", response_model=list[PolicyResponse])
def list_policies(db: Session = Depends(get_db_session)) -> list[PolicyResponse]:
    policies = db.scalars(select(Policy).order_by(desc(Policy.created_at))).all()
    return [PolicyResponse.model_validate(policy, from_attributes=True) for policy in policies]


@v1_router.post("/admin/policies", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
def create_policy(payload: CreatePolicyRequest, db: Session = Depends(get_db_session)) -> PolicyResponse:
    try:
        validated_rules = PolicyRules.model_validate(payload.rules_json)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc

    policy = Policy(
        name=payload.name,
        version=payload.version,
        status="INACTIVE",
        rules_json=validated_rules.model_dump(mode="json"),
        created_by=payload.created_by,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.post("/admin/policies/validate", response_model=ValidatePolicyResponse)
def validate_policy(payload: ValidatePolicyRequest) -> ValidatePolicyResponse:
    try:
        PolicyRules.model_validate(payload.rules_json)
        return ValidatePolicyResponse(valid=True, errors=[], warnings=[])
    except ValidationError as exc:
        errors = [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]
        return ValidatePolicyResponse(valid=False, errors=errors, warnings=[])


@v1_router.post("/admin/policies/{policy_id}/activate", response_model=PolicyResponse)
def activate_policy(policy_id: UUID, db: Session = Depends(get_db_session)) -> PolicyResponse:
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")

    db.execute(update(Policy).values(status="INACTIVE"))
    db.execute(update(Policy).where(Policy.id == policy_id).values(status="ACTIVE"))
    db.commit()
    db.refresh(policy)
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.get("/admin/policies/active", response_model=PolicyResponse)
def get_active_policy(db: Session = Depends(get_db_session)) -> PolicyResponse:
    policy = db.scalar(
        select(Policy)
        .where(Policy.status == "ACTIVE")
        .order_by(desc(Policy.version), desc(Policy.created_at))
        .limit(1)
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active policy")
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.get("/admin/killswitch", response_model=KillSwitchResponse)
def get_kill_switch(db: Session = Depends(get_db_session)) -> KillSwitchResponse:
    kill_switch = get_or_init_kill_switch(db)
    return KillSwitchResponse.model_validate(kill_switch, from_attributes=True)


@v1_router.post("/admin/killswitch", response_model=KillSwitchResponse)
def update_kill_switch(payload: KillSwitchUpdateRequest, db: Session = Depends(get_db_session)) -> KillSwitchResponse:
    kill_switch = get_or_init_kill_switch(db)
    kill_switch.enabled = payload.enabled
    kill_switch.observe_only = payload.observe_only
    kill_switch.reason = payload.reason
    kill_switch.updated_by = payload.updated_by
    db.add(kill_switch)
    db.commit()
    db.refresh(kill_switch)
    return KillSwitchResponse.model_validate(kill_switch, from_attributes=True)


@v1_router.get("/admin/decisions", response_model=list[DecisionEventResponse])
def list_decisions(
    action_type: str | None = None,
    decision: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = 50,
    db: Session = Depends(get_db_session),
) -> list[DecisionEventResponse]:
    normalized_limit = min(max(limit, 1), 200)
    query = select(DecisionEvent)

    if action_type:
        query = query.where(DecisionEvent.action_type == action_type)
    if decision:
        query = query.where(DecisionEvent.decision == decision)
    if request_id:
        query = query.where(DecisionEvent.request_id == request_id)
    if user_id:
        query = query.where(DecisionEvent.action_payload_json["user_id"].astext == user_id)
    if from_ts:
        query = query.where(DecisionEvent.timestamp >= from_ts)
    if to_ts:
        query = query.where(DecisionEvent.timestamp <= to_ts)

    events = db.scalars(query.order_by(desc(DecisionEvent.timestamp)).limit(normalized_limit)).all()
    return [DecisionEventResponse.model_validate(event, from_attributes=True) for event in events]


@v1_router.get("/admin/decisions/export", response_model=list[DecisionEventResponse])
def export_decisions(
    action_type: str | None = None,
    decision: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = 100,
    db: Session = Depends(get_db_session),
) -> list[DecisionEventResponse]:
    normalized_limit = min(max(limit, 1), 1000)
    query = select(DecisionEvent)

    if action_type:
        query = query.where(DecisionEvent.action_type == action_type)
    if decision:
        query = query.where(DecisionEvent.decision == decision)
    if from_ts:
        query = query.where(DecisionEvent.timestamp >= from_ts)
    if to_ts:
        query = query.where(DecisionEvent.timestamp <= to_ts)

    events = db.scalars(query.order_by(desc(DecisionEvent.timestamp)).limit(normalized_limit)).all()
    return [DecisionEventResponse.model_validate(event, from_attributes=True) for event in events]


@v1_router.get("/admin/decisions/{event_id}", response_model=DecisionEventResponse)
def get_decision_detail(event_id: UUID, db: Session = Depends(get_db_session)) -> DecisionEventResponse:
    event = db.get(DecisionEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision event not found")
    return DecisionEventResponse.model_validate(event, from_attributes=True)


@v1_router.post("/admin/decisions/{event_id}/replay", response_model=DecisionReplayResponse)
def replay_decision(event_id: UUID, db: Session = Depends(get_db_session)) -> DecisionReplayResponse:
    event = db.get(DecisionEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision event not found")

    if event.policy_id is None or event.policy_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored decision does not reference a policy version",
        )

    policy = db.scalar(
        select(Policy).where(Policy.id == event.policy_id, Policy.version == event.policy_version).limit(1)
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored policy version referenced by decision was not found",
        )

    if event.action_payload_json is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored action payload is missing for replay",
        )

    try:
        amount = _extract_amount_from_payload(event.action_type, event.action_payload_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored action payload is invalid for replay: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    try:
        policy_rules = PolicyRules.model_validate(policy.rules_json)
        exposure_context = ExposureContext.model_validate(event.exposure_snapshot_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored policy or exposure snapshot is invalid for replay: {exc}",
        ) from exc

    replayed_decision, replayed_reason_codes, _ = evaluate_action(
        amount=amount,
        exposure_context=exposure_context,
        policy=policy_rules,
    )
    original_decision = event.would_decision if event.would_decision is not None else event.decision
    original_reason_codes = (
        event.would_reason_codes if event.would_reason_codes is not None else event.reason_codes
    )

    return DecisionReplayResponse(
        event_id=event.event_id,
        original_decision=event.decision,
        original_reason_codes=event.reason_codes,
        original_would_decision=event.would_decision,
        original_would_reason_codes=event.would_reason_codes,
        replayed_decision=replayed_decision,
        replayed_reason_codes=replayed_reason_codes,
        matches_original=(original_decision == replayed_decision and original_reason_codes == replayed_reason_codes),
    )


@v1_router.post("/admin/simulate", response_model=SimulationResponse)
def simulate_action(payload: SimulationRequest, db: Session = Depends(get_db_session)) -> SimulationResponse:
    policy_context = _load_simulation_policy(db, payload)
    exposure_context = _resolve_simulation_exposure(payload)
    amount = _extract_simulation_amount(payload)

    decision, reason_codes, _risk_metrics = evaluate_action(
        amount=amount,
        exposure_context=exposure_context,
        policy=policy_context.rules,
    )

    return SimulationResponse(
        action_type=payload.action_type,
        decision=decision,
        reason_codes=policy_context.base_reason_codes + reason_codes,
        policy_id=policy_context.policy_id,
        policy_version=policy_context.policy_version,
        exposure_context_used=exposure_context.model_dump(mode="json"),
    )


@v1_router.get("/admin/metrics/decisions", response_model=DecisionMetricsResponse)
def get_decision_metrics(
    action_type: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db_session),
) -> DecisionMetricsResponse:
    return _build_decision_metrics(db=db, action_type=action_type, from_ts=from_ts, to_ts=to_ts)


@v1_router.get("/admin/metrics/exposure", response_model=ExposureMetricsResponse)
def get_exposure_metrics(
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> ExposureMetricsResponse:
    return _build_exposure_metrics(exposure_store=exposure_store)


@v1_router.get("/admin/dashboard", response_model=DashboardResponse)
def get_dashboard(
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> DashboardResponse:
    kill_switch = get_or_init_kill_switch(db)
    active_policy = db.scalar(
        select(Policy)
        .where(Policy.status == "ACTIVE")
        .order_by(desc(Policy.version), desc(Policy.created_at))
        .limit(1)
    )
    recent_events = db.scalars(select(DecisionEvent).order_by(desc(DecisionEvent.timestamp)).limit(10)).all()

    return DashboardResponse(
        runtime_controls=DashboardRuntimeControls(
            kill_switch_enabled=kill_switch.enabled,
            observe_only=kill_switch.observe_only,
            reason=kill_switch.reason,
            updated_at=kill_switch.updated_at,
            updated_by=kill_switch.updated_by,
        ),
        active_policy=(
            DashboardActivePolicy(
                policy_id=active_policy.id,
                name=active_policy.name,
                version=active_policy.version,
                status=active_policy.status,
                rules_json=active_policy.rules_json,
            )
            if active_policy is not None
            else None
        ),
        decision_metrics=_build_decision_metrics(db=db),
        exposure_metrics=_build_exposure_metrics(exposure_store=exposure_store),
        recent_decisions=[DecisionEventResponse.model_validate(event, from_attributes=True) for event in recent_events],
    )


def _build_exposure_metrics(
    exposure_store: ExposureStore,
) -> ExposureMetricsResponse:
    date_bucket = datetime.now(timezone.utc).date()
    refund_exposure = exposure_store.get_exposure(action_type="refund", user_id="metrics", date=date_bucket)
    credit_exposure = exposure_store.get_exposure(
        action_type="credit_adjustment",
        user_id="metrics",
        date=date_bucket,
    )
    financial_total_amount_cents = exposure_store.get_financial_total(date_bucket)

    return ExposureMetricsResponse(
        date_bucket_utc=date_bucket.isoformat(),
        refund_daily_total_amount_cents=_decimal_to_cents(refund_exposure.daily_total_amount),
        credit_daily_total_amount_cents=_decimal_to_cents(credit_exposure.daily_total_amount),
        financial_total_amount_cents=financial_total_amount_cents,
    )


def _build_decision_metrics(
    db: Session,
    action_type: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> DecisionMetricsResponse:
    query = select(DecisionEvent)
    if action_type:
        query = query.where(DecisionEvent.action_type == action_type)
    if from_ts:
        query = query.where(DecisionEvent.timestamp >= from_ts)
    if to_ts:
        query = query.where(DecisionEvent.timestamp <= to_ts)

    events = db.scalars(query).all()

    counts_by_action_type = Counter(event.action_type for event in events)
    counts_by_reason_code: Counter[str] = Counter()
    for event in events:
        counts_by_reason_code.update(event.reason_codes)

    return DecisionMetricsResponse(
        total_decisions=len(events),
        allow_count=sum(1 for event in events if event.decision == "ALLOW"),
        escalate_count=sum(1 for event in events if event.decision == "ESCALATE"),
        block_count=sum(1 for event in events if event.decision == "BLOCK"),
        observe_only_count=sum(1 for event in events if "OBSERVE_ONLY" in event.reason_codes),
        would_block_count=sum(1 for event in events if event.would_decision == "BLOCK"),
        would_escalate_count=sum(1 for event in events if event.would_decision == "ESCALATE"),
        counts_by_action_type=dict(counts_by_action_type),
        counts_by_reason_code=dict(counts_by_reason_code),
    )


def _build_action_response(event: DecisionEvent) -> ActionDecisionResponse:
    return ActionDecisionResponse(
        request_id=event.request_id,
        decision=event.decision,
        reason_codes=event.reason_codes,
        policy_version=event.policy_version,
        model_version=event.model_version,
    )


def _serialize_payload(payload: BaseModel) -> dict[str, Any]:
    return payload.model_dump(mode="json")


def _extract_amount_from_payload(action_type: str, payload: dict[str, Any]) -> Decimal:
    if action_type == "refund":
        parsed = RefundActionRequest.model_validate(payload)
        return cents_to_decimal(parsed.refund_amount_cents)
    if action_type == "credit_adjustment":
        parsed = CreditActionRequest.model_validate(payload)
        return cents_to_decimal(parsed.credit_amount_cents)
    raise ValueError(f"Unsupported action_type for replay: {action_type}")


def _load_simulation_policy(db: Session, payload: SimulationRequest) -> ActivePolicy:
    if payload.policy_id is None or payload.policy_version is None:
        return load_active_policy(db)

    policy = db.scalar(
        select(Policy).where(Policy.id == payload.policy_id, Policy.version == payload.policy_version).limit(1)
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found for provided policy_id and policy_version",
        )
    return ActivePolicy(
        policy_id=policy.id,
        policy_version=policy.version,
        rules=PolicyRules.model_validate(policy.rules_json),
    )


def _resolve_simulation_exposure(payload: SimulationRequest) -> ExposureContext:
    if payload.exposure_override is None:
        return ExposureContext()

    return ExposureContext(
        daily_total_amount=cents_to_decimal(payload.exposure_override.daily_total_amount_cents),
        per_user_daily_count=payload.exposure_override.per_user_daily_count,
        per_user_daily_amount=cents_to_decimal(payload.exposure_override.per_user_daily_amount_cents),
        financial_total_amount_cents=payload.exposure_override.financial_total_amount_cents,
    )


def _extract_simulation_amount(payload: SimulationRequest) -> Decimal:
    if payload.action_type == "refund":
        if payload.refund is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="refund payload is required")
        return cents_to_decimal(payload.refund.refund_amount_cents)
    if payload.action_type == "credit_adjustment":
        if payload.credit is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="credit payload is required")
        return cents_to_decimal(payload.credit.credit_amount_cents)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported action_type")


def _decimal_to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _postgres_ready() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def _redis_ready() -> bool:
    try:
        client = Redis.from_url(get_settings().redis_url, decode_responses=True)
        return bool(client.ping())
    except Exception:
        return False
