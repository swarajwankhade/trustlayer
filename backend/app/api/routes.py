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
    DemoBootstrapResponse,
    DemoGenerateResponse,
    DemoResetResponse,
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
from app.devtools.service import bootstrap_demo_data, generate_demo_decisions, reset_dev_data
from app.evaluators import get_evaluator
from app.evaluators.refund_credit_v1 import RefundCreditV1Exposure
from app.exposure.store import ExposureStore, get_exposure_store
from app.models import DecisionEvent, Policy
from app.policies.schemas import ExposureContext, PolicyRules
from app.policies.service import ActivePolicy, load_active_policy

router = APIRouter()
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
DEFAULT_POLICY_TYPE = "refund_credit_v1"


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
    settings = get_settings()
    html_content = """
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
      h3 { margin: 14px 0 8px; font-size: 15px; color: #28384a; }
      .subtitle { margin: 0 0 20px; color: var(--muted); }
      .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 2px 8px rgba(16, 36, 64, 0.04);
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
      .form-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px;
      }
      .form-grid label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
      .input-small { min-width: 0; width: 100%; }
      .section-note { color: var(--muted); margin-top: 0; margin-bottom: 10px; }
      .row-active { background: #eef8f1; }
      .row-selected { background: #edf4ff; }
      .reason-tags { display: flex; flex-wrap: wrap; gap: 6px; }
      .id-line { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
      .id-pill {
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 12px;
        background: #f3f6fa;
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 4px 8px;
      }
      .helper {
        margin: 0 0 10px;
        color: var(--muted);
        font-size: 13px;
      }
      .json-block { margin-top: 10px; }
      details > summary {
        cursor: pointer;
        color: #2a3c51;
        font-weight: 600;
        margin-bottom: 8px;
      }
      .status-line {
        font-size: 13px;
        color: var(--muted);
        margin-top: 8px;
      }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>TrustLayer Operator Dashboard</h1>
      <p class="subtitle">Operational snapshot from <code>/v1/admin/dashboard</code> with runtime controls from <code>/v1/admin/killswitch</code>.</p>
      <div class="chips">
        <span class="chip">Service: TrustLayer</span>
        <span class="chip">Version: __SERVICE_VERSION__</span>
        <span class="chip">Environment: __APP_ENV__</span>
      </div>

      <section class="card">
        <div class="toolbar">
          <label>
            API Key
            <input id="apiKey" class="input" type="password" placeholder="X-API-Key" />
          </label>
          <button id="refreshBtn" class="button button-primary">Refresh Dashboard</button>
          <span class="muted">Stored in local browser localStorage for demo convenience.</span>
        </div>
        <div id="lastRefreshed" class="status-line">Last refreshed: never</div>
        <h3>System Status</h3>
        <div class="helper">Readiness from <code>/ready</code>.</div>
        <div class="chips">
          <span id="statusService" class="chip">Service readiness: unknown</span>
          <span id="statusPostgres" class="chip">Postgres: unknown</span>
          <span id="statusRedis" class="chip">Redis: unknown</span>
        </div>
        <div id="loadBanner" class="banner hidden"></div>
      </section>

      <div class="stack">
        <section class="card">
          <h2>Runtime Controls</h2>
          <p class="helper">Observe Only keeps responses as ALLOW while recording the underlying would-decision for safe rollout visibility.</p>
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
          <h2>Demo Helpers</h2>
          <p class="helper">Local/demo-only helpers to seed policy baseline, generate sample decisions, and reset demo state.</p>
          <div class="toolbar">
            <button id="seedDemoBtn" class="button">Seed Demo Policy</button>
            <button id="generateDemoBtn" class="button">Generate Demo Events</button>
            <button id="resetDemoBtn" class="button">Reset Demo Data</button>
          </div>
          <div id="demoHelpersBanner" class="banner hidden"></div>
          <details class="json-block" open>
            <summary>Demo Helper Result</summary>
            <pre id="demoHelpersResult">No demo helper action run yet.</pre>
          </details>
        </section>

        <section class="card">
          <h2>Active Policy</h2>
          <p class="helper">Current ACTIVE policy used by action evaluation.</p>
          <div id="policyBadges" class="chips"></div>
          <div id="activePolicyState" class="muted">Waiting for data.</div>
          <details class="json-block" open>
            <summary>Rules JSON</summary>
            <pre id="activePolicyRules">{}</pre>
          </details>
        </section>

        <section class="card">
          <h2>Policies</h2>
          <p class="helper">Policy history ordered newest first. Activate switches the active policy used by decisioning.</p>
          <div id="policiesBanner" class="banner hidden"></div>
          <table>
            <thead>
              <tr>
                <th>policy_id</th>
                <th>name</th>
                <th>version</th>
                <th>status</th>
                <th>created_at</th>
                <th>actions (View Rules / Activate)</th>
              </tr>
            </thead>
            <tbody id="policiesTableBody">
              <tr><td colspan="6" class="muted">Policies will load here.</td></tr>
            </tbody>
          </table>
          <details class="json-block" open>
            <summary>Policy Rules</summary>
            <pre id="policyRulesViewer">Select View Rules on a policy row to inspect rules_json.</pre>
          </details>
          <h3>Policy Diff</h3>
          <p class="helper">Compare two policy versions and view only changed rules fields.</p>
          <div class="form-grid">
            <label>
              first policy selector
              <select id="policyCompareA" class="input input-small"></select>
            </label>
            <label>
              second policy selector
              <select id="policyCompareB" class="input input-small"></select>
            </label>
          </div>
          <div class="toolbar" style="margin-top: 10px;">
            <button id="comparePoliciesBtn" class="button">Compare Policies</button>
          </div>
          <div id="policyDiffBanner" class="banner hidden"></div>
          <table>
            <thead>
              <tr>
                <th>field name</th>
                <th>old value</th>
                <th>new value</th>
              </tr>
            </thead>
            <tbody id="policyDiffBody">
              <tr><td colspan="3" class="muted">Select two policies and click Compare Policies.</td></tr>
            </tbody>
          </table>
        </section>

        <section class="card">
          <h2>Policy Editor</h2>
          <p class="section-note">Validate, create, and activate policies using existing admin APIs.</p>
          <div class="form-grid">
            <label>
              policy_name
              <input id="policyName" class="input input-small" type="text" placeholder="demo_policy" />
            </label>
            <label>
              policy_version
              <input id="policyVersion" class="input input-small" type="number" min="1" step="1" placeholder="1" />
            </label>
            <label>
              per_action_max_amount
              <input id="policyPerActionMax" class="input input-small" type="number" min="1" step="1" placeholder="10000" />
            </label>
            <label>
              daily_total_cap_amount
              <input id="policyDailyTotalCap" class="input input-small" type="number" min="1" step="1" placeholder="20000" />
            </label>
            <label>
              per_user_daily_count_cap
              <input id="policyPerUserCountCap" class="input input-small" type="number" min="1" step="1" placeholder="10" />
            </label>
            <label>
              per_user_daily_amount_cap
              <input id="policyPerUserAmountCap" class="input input-small" type="number" min="1" step="1" placeholder="20000" />
            </label>
            <label>
              near_cap_escalation_ratio
              <input id="policyNearCapRatio" class="input input-small" type="number" min="0" max="1" step="0.01" value="0.9" />
            </label>
            <label>
              activate_policy_id (optional override)
              <input id="activatePolicyId" class="input input-small" type="text" placeholder="uuid" />
            </label>
          </div>
          <div class="toolbar" style="margin-top: 10px;">
            <button id="validatePolicyBtn" class="button">Validate Policy</button>
            <button id="createPolicyBtn" class="button">Create Policy</button>
            <button id="activatePolicyBtn" class="button">Activate Policy</button>
          </div>
          <div id="policyEditorBanner" class="banner hidden"></div>
          <div id="policyEditorState" class="muted">No policy created in this session yet.</div>
          <pre id="policyValidationResult">Validation result will appear here.</pre>
        </section>

        <section class="card">
          <h2>Decision Metrics</h2>
          <p class="helper">Aggregate decision outcomes from the decision ledger.</p>
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
          <p class="helper">Current Redis exposure counters for today (UTC date bucket).</p>
          <div id="exposureMetricsGrid" class="metrics"></div>
        </section>

        <section class="card">
          <h2>Simulation</h2>
          <p class="section-note">Dry-run evaluator call via <code>POST /v1/admin/simulate</code>. No decision event write and no Redis exposure mutation.</p>
          <div class="form-grid">
            <label>
              Action Type
              <select id="simActionType" class="input input-small">
                <option value="refund">refund</option>
                <option value="credit_adjustment">credit_adjustment</option>
              </select>
            </label>
            <label>
              User ID
              <input id="simUserId" class="input input-small" type="text" placeholder="user_123" />
            </label>
            <label id="simRefundAmountWrap">
              Refund Amount (cents)
              <input id="simRefundAmount" class="input input-small" type="number" min="1" step="1" placeholder="1000" />
            </label>
            <label id="simCreditAmountWrap" class="hidden">
              Credit Amount (cents)
              <input id="simCreditAmount" class="input input-small" type="number" min="1" step="1" placeholder="1000" />
            </label>
            <label>
              Currency
              <input id="simCurrency" class="input input-small" type="text" value="USD" maxlength="3" />
            </label>
            <label>
              Ticket ID (optional)
              <input id="simTicketId" class="input input-small" type="text" placeholder="ticket_001" />
            </label>
            <label id="simCreditTypeWrap" class="hidden">
              Credit Type (optional)
              <input id="simCreditType" class="input input-small" type="text" placeholder="goodwill" />
            </label>
            <label>
              Model Version (optional)
              <input id="simModelVersion" class="input input-small" type="text" placeholder="model-v1" />
            </label>
          </div>
          <h3>Exposure Overrides (optional)</h3>
          <div class="form-grid">
            <label>
              financial_total_amount_cents
              <input id="simFinancialTotal" class="input input-small" type="number" min="0" step="1" />
            </label>
            <label>
              daily_total_amount_cents
              <input id="simDailyTotal" class="input input-small" type="number" min="0" step="1" />
            </label>
            <label>
              per_user_daily_count
              <input id="simPerUserCount" class="input input-small" type="number" min="0" step="1" />
            </label>
            <label>
              per_user_daily_amount_cents
              <input id="simPerUserAmount" class="input input-small" type="number" min="0" step="1" />
            </label>
          </div>
          <div class="toolbar" style="margin-top: 10px;">
            <button id="runSimulationBtn" class="button">Run Simulation</button>
          </div>
          <div id="simulationBanner" class="banner hidden"></div>
          <details class="json-block" open>
            <summary>Simulation Result</summary>
            <pre id="simulationResult">No simulation run yet.</pre>
          </details>
        </section>

        <section class="card">
          <h2>Recent Decisions</h2>
          <p class="helper">Newest decision events first. Use filters for targeted inspection, then View or Replay any row. Detail and replay panels below track the selected decision.</p>
          <div class="form-grid" style="margin-bottom: 10px;">
            <label>
              action_type
              <select id="filterActionType" class="input input-small">
                <option value="">all</option>
                <option value="refund">refund</option>
                <option value="credit_adjustment">credit_adjustment</option>
              </select>
            </label>
            <label>
              decision
              <select id="filterDecision" class="input input-small">
                <option value="">all</option>
                <option value="ALLOW">ALLOW</option>
                <option value="ESCALATE">ESCALATE</option>
                <option value="BLOCK">BLOCK</option>
              </select>
            </label>
            <label>
              request_id
              <input id="filterRequestId" class="input input-small" type="text" placeholder="optional request_id" />
            </label>
          </div>
          <div class="toolbar" style="margin-bottom: 10px;">
            <button id="applyFiltersBtn" class="button">Apply Filters</button>
            <button id="clearFiltersBtn" class="button">Clear Filters</button>
          </div>
          <div id="decisionFiltersBanner" class="banner hidden"></div>
          <table>
            <thead>
              <tr>
                <th>timestamp</th>
                <th>action_type</th>
                <th>decision</th>
                <th>would_decision</th>
                <th>reason_codes</th>
                <th>actions (View / Replay)</th>
              </tr>
            </thead>
            <tbody id="recentDecisionsBody">
              <tr><td colspan="6" class="muted">No data loaded yet.</td></tr>
            </tbody>
          </table>
          <div class="toolbar" style="margin-top: 10px;">
            <button id="loadMoreDecisionsBtn" class="button">Load More</button>
          </div>
          <div class="grid" style="margin-top: 12px;">
            <div class="card" style="padding: 12px;">
              <h3>Decision Detail</h3>
              <p class="helper">Stored event payload and exposure snapshot for the selected row.</p>
              <div id="selectedDecisionIds" class="id-line">
                <span class="muted">No decision selected yet.</span>
              </div>
              <div id="detailBanner" class="banner hidden"></div>
              <details class="json-block" open>
                <summary>Decision Detail JSON</summary>
                <pre id="decisionDetailResult">Select View from a recent decision row to inspect details.</pre>
              </details>
            </div>
            <div class="card" style="padding: 12px;">
              <h3>Replay Result</h3>
              <p class="helper">Replay recomputes a historical decision using stored policy version and exposure snapshot.</p>
              <div id="replayBanner" class="banner hidden"></div>
              <details class="json-block" open>
                <summary>Replay Result JSON</summary>
                <pre id="decisionReplayResult">Select Replay from a recent decision row to run deterministic replay.</pre>
              </details>
            </div>
          </div>
        </section>

        <section class="card">
          <h2>Export Decisions</h2>
          <p class="helper">Run filtered exports for offline audit/debug. JSON download uses the latest successful export result.</p>
          <div class="form-grid">
            <label>
              action_type
              <select id="exportActionType" class="input input-small">
                <option value="">all</option>
                <option value="refund">refund</option>
                <option value="credit_adjustment">credit_adjustment</option>
              </select>
            </label>
            <label>
              decision
              <select id="exportDecision" class="input input-small">
                <option value="">all</option>
                <option value="ALLOW">ALLOW</option>
                <option value="ESCALATE">ESCALATE</option>
                <option value="BLOCK">BLOCK</option>
              </select>
            </label>
            <label>
              from
              <input id="exportFrom" class="input input-small" type="datetime-local" />
            </label>
            <label>
              to
              <input id="exportTo" class="input input-small" type="datetime-local" />
            </label>
            <label>
              limit
              <input id="exportLimit" class="input input-small" type="number" min="1" max="1000" step="1" value="100" />
            </label>
          </div>
          <div class="toolbar" style="margin-top: 10px;">
            <button id="exportDecisionsBtn" class="button">Export Decisions</button>
            <button id="downloadExportBtn" class="button">Download JSON</button>
          </div>
          <div id="exportBanner" class="banner hidden"></div>
          <details class="json-block" open>
            <summary>Export Preview</summary>
            <pre id="exportResult">No export run yet.</pre>
          </details>
        </section>

      </div>
    </div>

    <script>
      const API_KEY_STORAGE_KEY = "trustlayer_admin_api_key";
      const apiKeyInput = document.getElementById("apiKey");
      const refreshBtn = document.getElementById("refreshBtn");
      const applyControlsBtn = document.getElementById("applyControlsBtn");
      const seedDemoBtn = document.getElementById("seedDemoBtn");
      const generateDemoBtn = document.getElementById("generateDemoBtn");
      const resetDemoBtn = document.getElementById("resetDemoBtn");
      const runSimulationBtn = document.getElementById("runSimulationBtn");
      const validatePolicyBtn = document.getElementById("validatePolicyBtn");
      const createPolicyBtn = document.getElementById("createPolicyBtn");
      const activatePolicyBtn = document.getElementById("activatePolicyBtn");
      const comparePoliciesBtn = document.getElementById("comparePoliciesBtn");
      const applyFiltersBtn = document.getElementById("applyFiltersBtn");
      const clearFiltersBtn = document.getElementById("clearFiltersBtn");
      const loadMoreDecisionsBtn = document.getElementById("loadMoreDecisionsBtn");
      const exportDecisionsBtn = document.getElementById("exportDecisionsBtn");
      const downloadExportBtn = document.getElementById("downloadExportBtn");
      const loadBanner = document.getElementById("loadBanner");
      const lastRefreshed = document.getElementById("lastRefreshed");
      const statusService = document.getElementById("statusService");
      const statusPostgres = document.getElementById("statusPostgres");
      const statusRedis = document.getElementById("statusRedis");
      const controlBanner = document.getElementById("controlBanner");
      const demoHelpersBanner = document.getElementById("demoHelpersBanner");
      const simulationBanner = document.getElementById("simulationBanner");
      const policyEditorBanner = document.getElementById("policyEditorBanner");
      const policiesBanner = document.getElementById("policiesBanner");
      const policyDiffBanner = document.getElementById("policyDiffBanner");
      const decisionFiltersBanner = document.getElementById("decisionFiltersBanner");
      const exportBanner = document.getElementById("exportBanner");
      const detailBanner = document.getElementById("detailBanner");
      const replayBanner = document.getElementById("replayBanner");
      const simActionType = document.getElementById("simActionType");
      let createdPolicyId = null;
      let latestExportData = null;
      let allPolicies = [];
      const RECENT_DECISIONS_PAGE_SIZE = 10;
      let recentDecisionsOffset = 0;
      let recentDecisionsHasMore = true;
      let currentDecisionFilters = { action_type: "", decision: "", request_id: "" };
      let selectedDecisionEventId = null;

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

      function formatJson(value) {
        if (value === null || value === undefined) {
          return "null";
        }
        return JSON.stringify(value, null, 2);
      }

      function chipClassForDecision(value) {
        if (value === "ALLOW") return "chip chip-good";
        if (value === "ESCALATE") return "chip chip-warn";
        if (value === "BLOCK") return "chip chip-bad";
        return "chip";
      }

      async function copyToClipboard(value, label) {
        if (!value) {
          return;
        }
        try {
          await navigator.clipboard.writeText(String(value));
          showBanner(detailBanner, `${label} copied.`, true);
        } catch {
          showBanner(detailBanner, `Unable to copy ${label}.`, false);
        }
      }

      function updateSelectedDecisionIds(detail) {
        const container = document.getElementById("selectedDecisionIds");
        container.innerHTML = "";
        if (!detail) {
          container.innerHTML = '<span class="muted">No decision selected yet.</span>';
          return;
        }

        const idEntries = [
          ["event_id", detail.event_id],
          ["request_id", detail.request_id],
          ["policy_id", detail.policy_id || "none"],
        ];
        for (const [label, value] of idEntries) {
          const pill = document.createElement("span");
          pill.className = "id-pill";
          pill.textContent = `${label}: ${value}`;
          container.appendChild(pill);

          if (value && value !== "none") {
            const copyBtn = document.createElement("button");
            copyBtn.className = "button";
            copyBtn.textContent = `Copy ${label}`;
            copyBtn.addEventListener("click", () => copyToClipboard(value, label));
            container.appendChild(copyBtn);
          }
        }
      }

      function markSelectedDecisionRow(eventId) {
        selectedDecisionEventId = eventId;
        const rows = document.querySelectorAll("#recentDecisionsBody tr");
        for (const row of rows) {
          if (row.getAttribute("data-event-id") === eventId) {
            row.classList.add("row-selected");
          } else {
            row.classList.remove("row-selected");
          }
        }
      }

      function renderReasonCodeChips(reasonCodes) {
        if (!reasonCodes || !reasonCodes.length) {
          return "-";
        }
        return `<div class="reason-tags">${reasonCodes.map((code) => `<span class="chip">${code}</span>`).join("")}</div>`;
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

      function setStatusChip(node, label, statusValue) {
        node.className = "chip";
        if (statusValue === "ok" || statusValue === "ready") {
          node.classList.add("chip-good");
        } else if (statusValue === "error" || statusValue === "degraded") {
          node.classList.add("chip-bad");
        }
        node.textContent = `${label}: ${statusValue}`;
      }

      async function refreshSystemStatus() {
        try {
          const response = await fetch("/ready");
          const data = await response.json();
          setStatusChip(statusService, "Service readiness", data.status || "unknown");
          setStatusChip(statusPostgres, "Postgres", data.postgres || "unknown");
          setStatusChip(statusRedis, "Redis", data.redis || "unknown");
        } catch {
          setStatusChip(statusService, "Service readiness", "error");
          setStatusChip(statusPostgres, "Postgres", "unknown");
          setStatusChip(statusRedis, "Redis", "unknown");
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
        rules.textContent = formatJson(policy.rules_json || {});
      }

      function renderRecentDecisions(items, append = false) {
        const tbody = document.getElementById("recentDecisionsBody");
        if (!append) {
          tbody.innerHTML = "";
        }
        if (!items.length) {
          if (!append) {
            tbody.innerHTML = '<tr><td colspan="6" class="muted">No recent decisions available.</td></tr>';
          }
          return;
        }

        const sorted = [...items].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        for (const item of sorted) {
          const tr = document.createElement("tr");
          tr.setAttribute("data-event-id", item.event_id);
          const decisionClass = chipClassForDecision(item.decision);
          const wouldDecisionClass = chipClassForDecision(item.would_decision);
          tr.innerHTML = `
            <td>${item.timestamp}</td>
            <td>${item.action_type}</td>
            <td><span class="${decisionClass}">${item.decision}</span></td>
            <td>${item.would_decision ? `<span class="${wouldDecisionClass}">${item.would_decision}</span>` : "-"}</td>
            <td>${renderReasonCodeChips(item.reason_codes || [])}</td>
            <td><button class="button" data-action="view">View</button> <button class="button" data-action="replay">Replay</button></td>
          `;
          if (item.event_id === selectedDecisionEventId) {
            tr.classList.add("row-selected");
          }
          tr.querySelector('[data-action="view"]').addEventListener("click", () => {
            markSelectedDecisionRow(item.event_id);
            loadDecisionDetail(item.event_id);
          });
          tr.querySelector('[data-action="replay"]').addEventListener("click", () => {
            markSelectedDecisionRow(item.event_id);
            replayDecision(item.event_id);
          });
          tbody.appendChild(tr);
        }
      }

      function getDecisionFilterValues() {
        return {
          action_type: document.getElementById("filterActionType").value,
          decision: document.getElementById("filterDecision").value,
          request_id: document.getElementById("filterRequestId").value.trim(),
        };
      }

      function setRecentDecisionsLoading(message) {
        const tbody = document.getElementById("recentDecisionsBody");
        tbody.innerHTML = `<tr><td colspan="6" class="muted">${message}</td></tr>`;
      }

      function updateLoadMoreButton() {
        loadMoreDecisionsBtn.disabled = !recentDecisionsHasMore;
        loadMoreDecisionsBtn.style.display = recentDecisionsHasMore ? "inline-block" : "none";
      }

      function resetRecentDecisionsPagination(filters) {
        currentDecisionFilters = { ...filters };
        recentDecisionsOffset = 0;
        recentDecisionsHasMore = true;
        updateLoadMoreButton();
      }

      async function loadRecentDecisions(filters, append = false) {
        hideBanner(decisionFiltersBanner);
        const params = new URLSearchParams();
        params.set("limit", String(RECENT_DECISIONS_PAGE_SIZE));
        params.set("offset", String(append ? recentDecisionsOffset : 0));
        if (filters.action_type) params.set("action_type", filters.action_type);
        if (filters.decision) params.set("decision", filters.decision);
        if (filters.request_id) params.set("request_id", filters.request_id);

        const response = await fetch(`/v1/admin/decisions?${params.toString()}`, { headers: getHeaders() });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Failed to load filtered decisions: ${JSON.stringify(data)}`;
          showBanner(decisionFiltersBanner, message, false);
          if (!append) {
            const tbody = document.getElementById("recentDecisionsBody");
            tbody.innerHTML = '<tr><td colspan="6" class="muted">Failed to load decisions with selected filters.</td></tr>';
          }
          return;
        }

        const rows = data || [];
        renderRecentDecisions(rows, append);
        if (!append && rows.length === 0) {
          selectedDecisionEventId = null;
          updateSelectedDecisionIds(null);
          recentDecisionsHasMore = false;
          updateLoadMoreButton();
          showBanner(decisionFiltersBanner, "No decisions found for current filters.", true);
          return;
        }

        recentDecisionsOffset = (append ? recentDecisionsOffset : 0) + rows.length;
        recentDecisionsHasMore = rows.length === RECENT_DECISIONS_PAGE_SIZE;
        updateLoadMoreButton();
        showBanner(decisionFiltersBanner, "Recent decisions updated.", true);
      }

      function buildExportQueryParams() {
        const params = new URLSearchParams();
        const actionType = document.getElementById("exportActionType").value;
        const decision = document.getElementById("exportDecision").value;
        const fromValue = document.getElementById("exportFrom").value;
        const toValue = document.getElementById("exportTo").value;
        const limitValue = parseOptionalInt("exportLimit");

        if (actionType) params.set("action_type", actionType);
        if (decision) params.set("decision", decision);
        if (fromValue) params.set("from", new Date(fromValue).toISOString());
        if (toValue) params.set("to", new Date(toValue).toISOString());
        if (limitValue !== null) {
          const normalized = Math.min(Math.max(limitValue, 1), 1000);
          params.set("limit", String(normalized));
        } else {
          params.set("limit", "100");
        }

        return params;
      }

      async function exportDecisions() {
        hideBanner(exportBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(exportBanner, "API key is required to export decisions.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);

        document.getElementById("exportResult").textContent = "Loading exported decisions...";
        const response = await fetch(`/v1/admin/decisions/export?${buildExportQueryParams().toString()}`, { headers: getHeaders() });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Export failed: ${JSON.stringify(data)}`;
          showBanner(exportBanner, message, false);
          latestExportData = null;
          document.getElementById("exportResult").textContent = "Export failed.";
          return;
        }

        latestExportData = data || [];
        if (!latestExportData.length) {
          showBanner(exportBanner, "Export completed with no matching results.", true);
          document.getElementById("exportResult").textContent = "No decision events found for the selected filters.";
          return;
        }

        showBanner(exportBanner, `Exported ${latestExportData.length} decision event(s).`, true);
        document.getElementById("exportResult").textContent = formatJson(latestExportData);
      }

      function downloadExportJson() {
        hideBanner(exportBanner);
        if (!latestExportData) {
          showBanner(exportBanner, "No export data available. Run Export Decisions first.", false);
          return;
        }
        const payload = JSON.stringify(latestExportData, null, 2);
        const blob = new Blob([payload], { type: "application/json" });
        const link = document.createElement("a");
        const timestamp = new Date().toISOString().replaceAll(":", "-");
        link.href = URL.createObjectURL(blob);
        link.download = `trustlayer-decisions-export-${timestamp}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
      }

      function toggleSimulationFields() {
        const isRefund = simActionType.value === "refund";
        document.getElementById("simRefundAmountWrap").classList.toggle("hidden", !isRefund);
        document.getElementById("simCreditAmountWrap").classList.toggle("hidden", isRefund);
        document.getElementById("simCreditTypeWrap").classList.toggle("hidden", isRefund);
      }

      function parseOptionalInt(inputId) {
        const raw = document.getElementById(inputId).value.trim();
        if (!raw) return null;
        const parsed = Number.parseInt(raw, 10);
        return Number.isFinite(parsed) ? parsed : null;
      }

      function buildSimulationPayload() {
        const actionType = simActionType.value;
        const userId = document.getElementById("simUserId").value.trim();
        const currency = document.getElementById("simCurrency").value.trim().toUpperCase();
        const ticketId = document.getElementById("simTicketId").value.trim();
        const modelVersion = document.getElementById("simModelVersion").value.trim();

        if (!userId) {
          throw new Error("user_id is required.");
        }
        if (!currency || currency.length !== 3) {
          throw new Error("currency must be a 3-letter code.");
        }

        const payload = { action_type: actionType };
        if (actionType === "refund") {
          const amount = parseOptionalInt("simRefundAmount");
          if (amount === null || amount <= 0) {
            throw new Error("refund_amount_cents must be a positive integer.");
          }
          payload.refund = {
            user_id: userId,
            refund_amount_cents: amount,
            currency: currency,
          };
          if (ticketId) payload.refund.ticket_id = ticketId;
          if (modelVersion) payload.refund.model_version = modelVersion;
        } else {
          const amount = parseOptionalInt("simCreditAmount");
          const creditType = document.getElementById("simCreditType").value.trim();
          if (amount === null || amount <= 0) {
            throw new Error("credit_amount_cents must be a positive integer.");
          }
          payload.credit = {
            user_id: userId,
            credit_amount_cents: amount,
            currency: currency,
          };
          if (creditType) payload.credit.credit_type = creditType;
          if (ticketId) payload.credit.ticket_id = ticketId;
          if (modelVersion) payload.credit.model_version = modelVersion;
        }

        const exposureOverride = {};
        const financialTotal = parseOptionalInt("simFinancialTotal");
        const dailyTotal = parseOptionalInt("simDailyTotal");
        const perUserCount = parseOptionalInt("simPerUserCount");
        const perUserAmount = parseOptionalInt("simPerUserAmount");

        if (financialTotal !== null) exposureOverride.financial_total_amount_cents = financialTotal;
        if (dailyTotal !== null) exposureOverride.daily_total_amount_cents = dailyTotal;
        if (perUserCount !== null) exposureOverride.per_user_daily_count = perUserCount;
        if (perUserAmount !== null) exposureOverride.per_user_daily_amount_cents = perUserAmount;

        if (Object.keys(exposureOverride).length > 0) {
          payload.exposure_override = exposureOverride;
        }
        return payload;
      }

      function renderSimulationResult(result) {
        const output = {
          action_type: result.action_type,
          decision: result.decision,
          reason_codes: result.reason_codes,
          policy_id: result.policy_id,
          policy_version: result.policy_version,
          exposure_context_used: result.exposure_context_used,
        };
        document.getElementById("simulationResult").textContent = formatJson(output);
      }

      async function runSimulation() {
        hideBanner(simulationBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(simulationBanner, "API key is required to run simulation.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);

        let payload;
        try {
          payload = buildSimulationPayload();
        } catch (error) {
          const message = error instanceof Error ? error.message : "Invalid simulation input.";
          showBanner(simulationBanner, message, false);
          return;
        }

        showBanner(simulationBanner, "Running simulation...", true);
        document.getElementById("simulationResult").textContent = "Loading simulation result...";
        const response = await fetch("/v1/admin/simulate", {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry simulation."
            : `Simulation failed: ${JSON.stringify(data)}`;
          showBanner(simulationBanner, message, false);
          document.getElementById("simulationResult").textContent = "No simulation result.";
          return;
        }

        showBanner(simulationBanner, "Simulation completed.", true);
        renderSimulationResult(data);
      }

      function parseRequiredPositiveInt(inputId, fieldName) {
        const parsed = parseOptionalInt(inputId);
        if (parsed === null || parsed <= 0) {
          throw new Error(`${fieldName} must be a positive integer.`);
        }
        return parsed;
      }

      function buildPolicyPayload() {
        const name = document.getElementById("policyName").value.trim();
        const version = parseRequiredPositiveInt("policyVersion", "policy_version");
        const rulesJson = {
          per_action_max_amount: parseRequiredPositiveInt("policyPerActionMax", "per_action_max_amount"),
          daily_total_cap_amount: parseRequiredPositiveInt("policyDailyTotalCap", "daily_total_cap_amount"),
          per_user_daily_count_cap: parseRequiredPositiveInt("policyPerUserCountCap", "per_user_daily_count_cap"),
          per_user_daily_amount_cap: parseRequiredPositiveInt("policyPerUserAmountCap", "per_user_daily_amount_cap"),
          near_cap_escalation_ratio: Number.parseFloat(document.getElementById("policyNearCapRatio").value),
        };

        if (!name) {
          throw new Error("policy_name is required.");
        }
        if (!Number.isFinite(rulesJson.near_cap_escalation_ratio)) {
          throw new Error("near_cap_escalation_ratio must be a valid number.");
        }
        if (rulesJson.near_cap_escalation_ratio < 0 || rulesJson.near_cap_escalation_ratio > 1) {
          throw new Error("near_cap_escalation_ratio must be between 0 and 1.");
        }

        return {
          name: name,
          version: version,
          rules_json: rulesJson,
          created_by: document.getElementById("updatedBy").value.trim() || "operator-ui",
        };
      }

      function setPolicyDiffMessage(message) {
        const tbody = document.getElementById("policyDiffBody");
        tbody.innerHTML = `<tr><td colspan="3" class="muted">${message}</td></tr>`;
      }

      function populatePolicyCompareSelectors(policies) {
        const selectorA = document.getElementById("policyCompareA");
        const selectorB = document.getElementById("policyCompareB");
        const previousA = selectorA.value;
        const previousB = selectorB.value;

        for (const selector of [selectorA, selectorB]) {
          selector.innerHTML = "";
          const placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent = "Select policy";
          selector.appendChild(placeholder);
        }

        for (const policy of policies) {
          const policyId = policy.policy_id || policy.id;
          const label = `${policy.name} v${policy.version} (${policyId})${policy.status === "ACTIVE" ? " [ACTIVE]" : ""}`;
          const optionA = document.createElement("option");
          optionA.value = policyId;
          optionA.textContent = label;
          selectorA.appendChild(optionA);

          const optionB = document.createElement("option");
          optionB.value = policyId;
          optionB.textContent = label;
          selectorB.appendChild(optionB);
        }

        selectorA.value = policies.some((policy) => (policy.policy_id || policy.id) === previousA) ? previousA : "";
        selectorB.value = policies.some((policy) => (policy.policy_id || policy.id) === previousB) ? previousB : "";

        if (policies.length < 2) {
          setPolicyDiffMessage("Need at least two policies to compare.");
        } else {
          setPolicyDiffMessage("Select two policies and click Compare Policies.");
        }
      }

      function renderPoliciesTable(policies) {
        const tbody = document.getElementById("policiesTableBody");
        tbody.innerHTML = "";
        if (!policies.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="muted">No policies found.</td></tr>';
          return;
        }

        for (const policy of policies) {
          const tr = document.createElement("tr");
          if (policy.status === "ACTIVE" || policy.is_active) {
            tr.className = "row-active";
          }
          const policyId = policy.policy_id || policy.id;
          tr.innerHTML = `
            <td>${policyId}</td>
            <td>${policy.name}</td>
            <td>${policy.version}</td>
            <td>${policy.status}</td>
            <td>${policy.created_at || "-"}</td>
            <td><button class="button" data-action="view-rules">View Rules</button> <button class="button" data-action="activate">Activate</button></td>
          `;
          tr.querySelector('[data-action="view-rules"]').addEventListener("click", () => {
            document.getElementById("policyRulesViewer").textContent = formatJson(policy.rules_json || {});
          });
          tr.querySelector('[data-action="activate"]').addEventListener("click", async () => {
            await activatePolicyById(String(policyId), true);
          });
          tbody.appendChild(tr);
        }
      }

      function comparePolicies() {
        hideBanner(policyDiffBanner);
        if (allPolicies.length < 2) {
          showBanner(policyDiffBanner, "Need at least two policies to compare.", false);
          setPolicyDiffMessage("Need at least two policies to compare.");
          return;
        }

        const firstId = document.getElementById("policyCompareA").value;
        const secondId = document.getElementById("policyCompareB").value;
        if (!firstId || !secondId) {
          showBanner(policyDiffBanner, "Select two policies before comparing.", false);
          return;
        }
        if (firstId === secondId) {
          showBanner(policyDiffBanner, "Choose two different policies to compare.", false);
          return;
        }

        const first = allPolicies.find((policy) => (policy.policy_id || policy.id) === firstId);
        const second = allPolicies.find((policy) => (policy.policy_id || policy.id) === secondId);
        if (!first || !second) {
          showBanner(policyDiffBanner, "Selected policy not found. Refresh and try again.", false);
          return;
        }

        const firstRules = first.rules_json || {};
        const secondRules = second.rules_json || {};
        const keys = [...new Set([...Object.keys(firstRules), ...Object.keys(secondRules)])].sort();
        const changes = keys.filter(
          (key) => JSON.stringify(firstRules[key]) !== JSON.stringify(secondRules[key])
        );

        const tbody = document.getElementById("policyDiffBody");
        tbody.innerHTML = "";
        if (!changes.length) {
          setPolicyDiffMessage("No differences.");
          showBanner(policyDiffBanner, "No differences between selected policies.", true);
          return;
        }

        for (const key of changes) {
          const tr = document.createElement("tr");
          const fieldCell = document.createElement("td");
          const oldCell = document.createElement("td");
          const newCell = document.createElement("td");
          fieldCell.textContent = key;
          oldCell.textContent = formatJson(firstRules[key]);
          newCell.textContent = formatJson(secondRules[key]);
          tr.appendChild(fieldCell);
          tr.appendChild(oldCell);
          tr.appendChild(newCell);
          tbody.appendChild(tr);
        }
        showBanner(policyDiffBanner, `Comparison complete. ${changes.length} field(s) differ.`, true);
      }

      async function loadPolicies() {
        hideBanner(policiesBanner);
        const response = await fetch("/v1/admin/policies", { headers: getHeaders() });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Failed to load policies: ${JSON.stringify(data)}`;
          showBanner(policiesBanner, message, false);
          document.getElementById("policiesTableBody").innerHTML = '<tr><td colspan="6" class="muted">Failed to load policies.</td></tr>';
          allPolicies = [];
          populatePolicyCompareSelectors([]);
          return;
        }

        allPolicies = data || [];
        renderPoliciesTable(allPolicies);
        populatePolicyCompareSelectors(allPolicies);
        showBanner(policiesBanner, "Policies loaded.", true);
      }

      async function validatePolicy() {
        hideBanner(policyEditorBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(policyEditorBanner, "API key is required to validate policy.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);

        let payload;
        try {
          payload = buildPolicyPayload();
        } catch (error) {
          const message = error instanceof Error ? error.message : "Invalid policy input.";
          showBanner(policyEditorBanner, message, false);
          return;
        }

        document.getElementById("policyValidationResult").textContent = "Validating policy...";
        const response = await fetch("/v1/admin/policies/validate", {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify({ rules_json: payload.rules_json }),
        });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Policy validation failed: ${JSON.stringify(data)}`;
          showBanner(policyEditorBanner, message, false);
          document.getElementById("policyValidationResult").textContent = "Validation failed.";
          return;
        }

        const validationOutput = {
          valid: data.valid,
          errors: data.errors || [],
          warnings: data.warnings || [],
        };
        document.getElementById("policyValidationResult").textContent = formatJson(validationOutput);
        showBanner(
          policyEditorBanner,
          data.valid ? "Policy validation: valid." : "Policy validation: invalid. Review errors below.",
          data.valid
        );
      }

      async function createPolicy() {
        hideBanner(policyEditorBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(policyEditorBanner, "API key is required to create policy.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);

        let payload;
        try {
          payload = buildPolicyPayload();
        } catch (error) {
          const message = error instanceof Error ? error.message : "Invalid policy input.";
          showBanner(policyEditorBanner, message, false);
          return;
        }

        const response = await fetch("/v1/admin/policies", {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Policy creation failed: ${JSON.stringify(data)}`;
          showBanner(policyEditorBanner, message, false);
          return;
        }

        createdPolicyId = data.id;
        document.getElementById("activatePolicyId").value = createdPolicyId;
        document.getElementById("policyEditorState").textContent = `Created policy_id: ${createdPolicyId}`;
        showBanner(policyEditorBanner, `Policy created successfully. policy_id=${createdPolicyId}`, true);
      }

      async function activatePolicyById(policyId, fromRow = false) {
        hideBanner(policyEditorBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(policyEditorBanner, "API key is required to activate policy.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);
        if (!policyId) {
          showBanner(policyEditorBanner, "No policy_id available. Create a policy first or provide activate_policy_id.", false);
          return;
        }
        if (!confirm(`Activate policy ${policyId}?`)) {
          return;
        }

        const response = await fetch(`/v1/admin/policies/${policyId}/activate`, {
          method: "POST",
          headers: getHeaders(),
        });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Policy activation failed: ${JSON.stringify(data)}`;
          showBanner(policyEditorBanner, message, false);
          return;
        }

        showBanner(policyEditorBanner, `Policy activated successfully. policy_id=${data.id}`, true);
        if (fromRow) {
          showBanner(policiesBanner, `Policy activated successfully. policy_id=${data.id}`, true);
        }
        await refreshDashboard();
      }

      async function activatePolicy() {
        const manualPolicyId = document.getElementById("activatePolicyId").value.trim();
        const policyId = manualPolicyId || createdPolicyId;
        await activatePolicyById(policyId);
      }

      function setLoadingState() {
        selectedDecisionEventId = null;
        updateSelectedDecisionIds(null);
        document.getElementById("runtimeText").textContent = "Loading runtime controls...";
        document.getElementById("activePolicyState").textContent = "Loading policy...";
        document.getElementById("activePolicyRules").textContent = "{}";
        document.getElementById("policiesTableBody").innerHTML = '<tr><td colspan="6" class="muted">Loading policies...</td></tr>';
        document.getElementById("byActionType").textContent = "{}";
        document.getElementById("byReasonCode").textContent = "{}";
        renderMetricGrid("decisionMetricsGrid", [["total_decisions", "..."]]);
        renderMetricGrid("exposureMetricsGrid", [["financial_total_amount_cents", "..."]]);
        setRecentDecisionsLoading("Loading recent decisions...");
      }

      async function loadDecisionDetail(eventId) {
        markSelectedDecisionRow(eventId);
        hideBanner(detailBanner);
        document.getElementById("decisionDetailResult").textContent = "Loading decision detail...";
        const response = await fetch(`/v1/admin/decisions/${eventId}`, { headers: getHeaders() });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Failed to load decision detail: ${JSON.stringify(data)}`;
          showBanner(detailBanner, message, false);
          document.getElementById("decisionDetailResult").textContent = "Decision detail unavailable.";
          return;
        }

        const formatted = {
          event_id: data.event_id,
          timestamp: data.timestamp,
          action_type: data.action_type,
          request_id: data.request_id,
          decision: data.decision,
          reason_codes: data.reason_codes,
          would_decision: data.would_decision,
          would_reason_codes: data.would_reason_codes,
          policy_id: data.policy_id,
          policy_version: data.policy_version,
          exposure_snapshot_json: data.exposure_snapshot_json,
          action_payload_json: data.action_payload_json,
        };
        updateSelectedDecisionIds(formatted);
        document.getElementById("decisionDetailResult").textContent = formatJson(formatted);
        showBanner(detailBanner, "Decision detail loaded.", true);
      }

      async function replayDecision(eventId) {
        markSelectedDecisionRow(eventId);
        hideBanner(replayBanner);
        document.getElementById("decisionReplayResult").textContent = "Running replay...";
        const response = await fetch(`/v1/admin/decisions/${eventId}/replay`, {
          method: "POST",
          headers: getHeaders(),
        });
        const data = await response.json();
        if (!response.ok) {
          const message = response.status === 401
            ? "Invalid API key. Update the key and retry."
            : `Replay failed: ${JSON.stringify(data)}`;
          showBanner(replayBanner, message, false);
          document.getElementById("decisionReplayResult").textContent = "Replay result unavailable.";
          return;
        }

        const formatted = {
          event_id: data.event_id,
          original_decision: data.original_decision,
          replayed_decision: data.replayed_decision,
          matches_original: data.matches_original,
          original_reason_codes: data.original_reason_codes,
          replayed_reason_codes: data.replayed_reason_codes,
        };
        document.getElementById("decisionReplayResult").textContent = formatJson(formatted);
        showBanner(
          replayBanner,
          data.matches_original ? "Replay matched original decision." : "Replay differs from original decision.",
          data.matches_original
        );
      }

      async function refreshDashboard() {
        await refreshSystemStatus();
        hideBanner(controlBanner);
        hideBanner(loadBanner);
        hideBanner(policiesBanner);
        hideBanner(policyDiffBanner);
        hideBanner(decisionFiltersBanner);
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
        lastRefreshed.textContent = `Last refreshed: ${new Date().toLocaleString()}`;

        renderRuntimeControls(data.runtime_controls);
        renderActivePolicy(data.active_policy);
        await loadPolicies();
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
        document.getElementById("byActionType").textContent = formatJson(data.decision_metrics.counts_by_action_type || {});
        document.getElementById("byReasonCode").textContent = formatJson(data.decision_metrics.counts_by_reason_code || {});
        resetRecentDecisionsPagination(getDecisionFilterValues());
        await loadRecentDecisions(currentDecisionFilters);

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

      async function runDemoHelper(endpoint, actionLabel) {
        hideBanner(demoHelpersBanner);
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(demoHelpersBanner, "API key is required for demo helpers.", false);
          return;
        }
        if (actionLabel === "Reset Demo Data" && !confirm("Reset Demo Data will clear policies, decisions, and local exposure state. Continue?")) {
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);

        seedDemoBtn.disabled = true;
        generateDemoBtn.disabled = true;
        resetDemoBtn.disabled = true;
        document.getElementById("demoHelpersResult").textContent = `Running ${actionLabel}...`;

        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: getHeaders(),
          });
          const data = await response.json();
          if (!response.ok) {
            const message = response.status === 401
              ? "Invalid API key. Update the key and retry."
              : `${actionLabel} failed: ${JSON.stringify(data)}`;
            showBanner(demoHelpersBanner, message, false);
            document.getElementById("demoHelpersResult").textContent = `${actionLabel} failed.`;
            return;
          }

          document.getElementById("demoHelpersResult").textContent = formatJson(data);
          showBanner(demoHelpersBanner, `${actionLabel} completed.`, true);
          await refreshDashboard();
        } finally {
          seedDemoBtn.disabled = false;
          generateDemoBtn.disabled = false;
          resetDemoBtn.disabled = false;
        }
      }

      refreshBtn.addEventListener("click", refreshDashboard);
      applyControlsBtn.addEventListener("click", applyControls);
      seedDemoBtn.addEventListener("click", () => runDemoHelper("/v1/admin/demo/bootstrap", "Seed Demo Policy"));
      generateDemoBtn.addEventListener("click", () => runDemoHelper("/v1/admin/demo/generate", "Generate Demo Events"));
      resetDemoBtn.addEventListener("click", () => runDemoHelper("/v1/admin/demo/reset", "Reset Demo Data"));
      runSimulationBtn.addEventListener("click", runSimulation);
      validatePolicyBtn.addEventListener("click", validatePolicy);
      createPolicyBtn.addEventListener("click", createPolicy);
      activatePolicyBtn.addEventListener("click", activatePolicy);
      comparePoliciesBtn.addEventListener("click", comparePolicies);
      exportDecisionsBtn.addEventListener("click", exportDecisions);
      downloadExportBtn.addEventListener("click", downloadExportJson);
      applyFiltersBtn.addEventListener("click", async () => {
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(decisionFiltersBanner, "API key is required to apply filters.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);
        resetRecentDecisionsPagination(getDecisionFilterValues());
        setRecentDecisionsLoading("Loading filtered decisions...");
        await loadRecentDecisions(currentDecisionFilters);
      });
      clearFiltersBtn.addEventListener("click", async () => {
        document.getElementById("filterActionType").value = "";
        document.getElementById("filterDecision").value = "";
        document.getElementById("filterRequestId").value = "";
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(decisionFiltersBanner, "API key is required to reload decisions.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);
        resetRecentDecisionsPagination(getDecisionFilterValues());
        setRecentDecisionsLoading("Loading recent decisions...");
        await loadRecentDecisions(currentDecisionFilters);
      });
      loadMoreDecisionsBtn.addEventListener("click", async () => {
        if (!recentDecisionsHasMore) {
          return;
        }
        const key = apiKeyInput.value.trim();
        if (!key) {
          showBanner(decisionFiltersBanner, "API key is required to load more decisions.", false);
          return;
        }
        localStorage.setItem(API_KEY_STORAGE_KEY, key);
        loadMoreDecisionsBtn.disabled = true;
        loadMoreDecisionsBtn.textContent = "Loading...";
        try {
          await loadRecentDecisions(currentDecisionFilters, true);
        } finally {
          loadMoreDecisionsBtn.textContent = "Load More";
          updateLoadMoreButton();
        }
      });
      simActionType.addEventListener("change", toggleSimulationFields);
      toggleSimulationFields();

      const savedApiKey = localStorage.getItem(API_KEY_STORAGE_KEY);
      if (savedApiKey) {
        apiKeyInput.value = savedApiKey;
        refreshDashboard();
      } else {
        refreshSystemStatus();
      }
    </script>
  </body>
</html>
    """
    html_content = html_content.replace("__SERVICE_VERSION__", settings.service_version)
    html_content = html_content.replace("__APP_ENV__", settings.app_env)
    return HTMLResponse(html_content)


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
        policy_type=DEFAULT_POLICY_TYPE,
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
        evaluator = get_evaluator(DEFAULT_POLICY_TYPE)
        evaluator.validate_rules(payload.rules_json)
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


@v1_router.post("/admin/demo/bootstrap", response_model=DemoBootstrapResponse)
def demo_bootstrap(db: Session = Depends(get_db_session)) -> DemoBootstrapResponse:
    result = bootstrap_demo_data(
        db,
        activate_policy=True,
        created_by="admin-demo-helper",
    )
    return DemoBootstrapResponse(
        created_kill_switch=result.created_kill_switch,
        created_policy=result.created_policy,
        activated_policy=result.activated_policy,
        policy_id=result.policy_id,
        policy_version=result.policy_version,
    )


@v1_router.post("/admin/demo/generate", response_model=DemoGenerateResponse)
def demo_generate(
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> DemoGenerateResponse:
    result = generate_demo_decisions(db, exposure_store=exposure_store)
    return DemoGenerateResponse(
        generated_count=result.generated_count,
        request_ids=result.request_ids,
        decisions=result.decisions,
    )


@v1_router.post("/admin/demo/reset", response_model=DemoResetResponse)
def demo_reset(db: Session = Depends(get_db_session)) -> DemoResetResponse:
    result = reset_dev_data(
        db,
        redis_url=get_settings().redis_url,
        updated_by="admin-demo-helper",
    )
    return DemoResetResponse(
        decision_events_deleted=result.decision_events_deleted,
        policies_deleted=result.policies_deleted,
        redis_keys_deleted=result.redis_keys_deleted,
        kill_switch_enabled=result.kill_switch_enabled,
    )


@v1_router.get("/admin/decisions", response_model=list[DecisionEventResponse])
def list_decisions(
    action_type: str | None = None,
    decision: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db_session),
) -> list[DecisionEventResponse]:
    normalized_limit = min(max(limit, 1), 200)
    normalized_offset = max(offset, 0)
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

    events = db.scalars(
        query.order_by(desc(DecisionEvent.timestamp)).offset(normalized_offset).limit(normalized_limit)
    ).all()
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

    resolved_policy_type = event.policy_type or policy.policy_type or DEFAULT_POLICY_TYPE
    evaluator = get_evaluator(resolved_policy_type)
    try:
        normalized_action = evaluator.normalize_action(
            action_type=event.action_type,
            payload=event.action_payload_json,
        )
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored action payload is invalid for replay: {exc}",
        ) from exc

    try:
        typed_rules = evaluator.validate_rules(policy.rules_json)
        exposure_context = _deserialize_exposure_context(event.exposure_snapshot_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored policy or exposure snapshot is invalid for replay: {exc}",
        ) from exc

    replayed = evaluator.evaluate(
        action=normalized_action,
        exposure_context=_to_typed_exposure(exposure_context),
        rules=typed_rules,
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
        replayed_decision=replayed.decision,
        replayed_reason_codes=replayed.reason_codes,
        matches_original=(original_decision == replayed.decision and original_reason_codes == replayed.reason_codes),
    )


@v1_router.post("/admin/simulate", response_model=SimulationResponse)
def simulate_action(payload: SimulationRequest, db: Session = Depends(get_db_session)) -> SimulationResponse:
    policy_context = _load_simulation_policy(db, payload)
    exposure_context = _resolve_simulation_exposure(payload)
    policy_type = policy_context.policy_type or DEFAULT_POLICY_TYPE
    evaluator = get_evaluator(policy_type)
    typed_rules = evaluator.validate_rules(policy_context.rules.model_dump(mode="json"))
    normalized_action = evaluator.normalize_action(
        action_type=payload.action_type,
        payload=_extract_simulation_action_payload(payload),
    )

    evaluated = evaluator.evaluate(
        action=normalized_action,
        exposure_context=_to_typed_exposure(exposure_context),
        rules=typed_rules,
    )

    return SimulationResponse(
        action_type=payload.action_type,
        decision=evaluated.decision,
        reason_codes=policy_context.base_reason_codes + evaluated.reason_codes,
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
                policy_type=active_policy.policy_type,
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
        policy_type=policy.policy_type or DEFAULT_POLICY_TYPE,
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


def _extract_simulation_action_payload(payload: SimulationRequest) -> dict[str, Any]:
    if payload.action_type == "refund":
        if payload.refund is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="refund payload is required")
        return payload.refund.model_dump(mode="json")
    if payload.action_type == "credit_adjustment":
        if payload.credit is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="credit payload is required")
        return payload.credit.model_dump(mode="json")
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported action_type")


def _decimal_to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _to_typed_exposure(exposure_context: ExposureContext) -> RefundCreditV1Exposure:
    return RefundCreditV1Exposure(
        daily_total_amount_cents=_decimal_to_cents(exposure_context.daily_total_amount),
        per_user_daily_count=exposure_context.per_user_daily_count,
        per_user_daily_amount_cents=_decimal_to_cents(exposure_context.per_user_daily_amount),
        financial_total_amount_cents=exposure_context.financial_total_amount_cents,
    )


def _deserialize_exposure_context(exposure_snapshot: dict[str, Any]) -> ExposureContext:
    if "daily_total_amount" in exposure_snapshot:
        return ExposureContext.model_validate(exposure_snapshot)
    typed = RefundCreditV1Exposure.model_validate(exposure_snapshot)
    return ExposureContext(
        daily_total_amount=cents_to_decimal(typed.daily_total_amount_cents),
        per_user_daily_count=typed.per_user_daily_count,
        per_user_daily_amount=cents_to_decimal(typed.per_user_daily_amount_cents),
        financial_total_amount_cents=typed.financial_total_amount_cents,
    )


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
