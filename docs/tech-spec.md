# TrustLayer — MVP Technical Specification
AI Execution Control Plane for Autonomous Actions

Status: Draft v0.4  
Author: Swaraj  
Last Updated: 2026-03

---

# 1. Summary

TrustLayer is a middleware **AI execution control plane** that sits between AI agents and real‑world systems (billing, refunds, credits, account actions, infrastructure).

It evaluates proposed actions against deterministic policies and exposure limits and returns a decision:

ALLOW  
ESCALATE  
BLOCK

Every decision produces an immutable audit record.

Core concept:

AI → TrustLayer → Execution System

TrustLayer acts as a deterministic authorization boundary for probabilistic AI systems.

---

# 2. MVP Wedge

Phase 1 MVP governs two financial automation actions:

• Refund automation  
• Credit adjustments

These actions share the same decision pipeline and policy engine.

Primary responsibilities:

• Policy enforcement  
• Exposure tracking  
• Idempotent decision handling  
• Immutable decision logging  
• Operational kill switch

This MVP proves that organizations can safely increase AI autonomy when TrustLayer governs execution.

---

# 3. Key Capabilities

TrustLayer provides the following core capabilities.

## Decision Mediation

AI systems must request authorization before executing financial actions.

## Deterministic Policies

Policy rules define safe operating boundaries for AI automation.

## Exposure Tracking

TrustLayer tracks financial exposure across actions and users.

## Escalation Routing

Suspicious or near‑limit actions can be escalated to humans.

## Immutable Audit Log

Every decision is recorded with policy version and context.

## Operational Kill Switch

Operators can instantly disable AI automation.

---

# 4. MVP Goals

The MVP focuses on **Refund + Credit Governance**.

Capabilities:

• Evaluate refund and credit proposals  
• Enforce deterministic caps  
• Track exposure limits  
• Detect near‑cap escalation  
• Record decision events  
• Provide safe fallback behavior

---

# 5. Non‑Goals (MVP)

The MVP intentionally excludes:

• Complex policy DSL  
• Enterprise RBAC / SSO  
• Advanced ML anomaly detection  
• Fraud detection replacement  
• Payment execution rails

TrustLayer **authorizes actions but does not execute them**.

Execution occurs in downstream systems.

---

# 6. Core Action Types

MVP supports two action types.

refund  
credit_adjustment

Both actions pass through the same evaluation pipeline.

---

# 7. System Architecture (MVP)

Core components:

API Service  
Policy Engine  
Exposure Tracker  
Decision Log Store  
Kill Switch Control


High level flow:

AI Agent → TrustLayer → Customer System

---

# 8. Decision Pipeline

1 AI proposes action  
2 TrustLayer validates request  
3 Policy engine evaluates rules  
4 Exposure tracker calculates projected usage  
5 Decision produced  
6 Decision logged  
7 Response returned

---

# 9. API Specification

## POST /v1/actions/refund

Request

request_id (string, required)  
user_id (string, required)  
ticket_id (string, optional)  
refund_amount_cents (int, required)  
currency (string)  
model_version (string)  
metadata (json)

Response

request_id  
decision  
reason_codes  
policy_id  
policy_version

---

## POST /v1/actions/credit

Request

request_id (string, required)  
user_id (string, required)  
ticket_id (string, optional)  
credit_amount_cents (int, required)  
currency (string)  
credit_type (string)  
model_version (string)  
metadata (json)

Response identical to refund endpoint.

---

# 10. Policy Rules (MVP)

Policy rules stored as JSON.

Example schema:

per_action_max_amount  
daily_total_cap_amount  
per_user_daily_count_cap  
per_user_daily_amount_cap  
near_cap_escalation_ratio

---

# 11. Exposure Tracking

Exposure is tracked across both refunds and credits.

Tracked metrics:

• Global daily totals  
• Per‑user daily totals  
• Per‑user daily counts

Redis stores counters using UTC day buckets.

Example key pattern:

exposure:{action}:{date}:total

Counters increment only when decision = ALLOW.

---

# 12. Data Model

## policies

id  
version  
status  
rules_json  
created_at  
created_by


## decision_events

Append‑only ledger containing:

• event_id  
• timestamp  
• action_type  
• request_id  
• decision  
• reason_codes  
• model_version  
• policy_id  
• policy_version  
• exposure_snapshot_json  
• action_payload_json


## kill_switch

enabled  
reason  
updated_at  
updated_by

---

# 13. Failure Handling

Redis unavailable → ESCALATE

Postgres unavailable → fail safe

Network retries handled through request_id idempotency.

TrustLayer must **fail safe rather than fail open**.

---

# 14. Performance Targets

Decision latency targets:

p95 < 200ms  
p99 < 400ms

Availability target:

99.9%

---

# 15. Security

Authentication via API keys.

Security principles:

• least privilege  
• encrypted transport  
• append‑only decision logs  
• deterministic authorization

---

# 16. Operational Controls

Operators can:

• Enable kill switch  
• Modify policies  
• Inspect decision logs  
• Monitor exposure metrics

---

# 17. Future Evolution (Architecture Direction)

TrustLayer evolves across several phases.

Phase 1 — Refund + Credit Governance

Phase 2 — Multi‑Action Governance

Additional actions:

billing adjustments  
discounts  
account permissions  
subscription changes


Phase 3 — Autonomy Control Center

Operational command surface for AI autonomy.

Capabilities:

• exposure monitoring  
• policy simulation  
• incident timeline  
• automation dial


Phase 4 — System of Record

TrustLayer becomes the authoritative history of AI execution.

Features:

• deterministic replay  
• tamper evidence  
• append‑only automation ledger


Phase 5 — Trust Authority

Enterprise platform capabilities:

• RBAC  
• compliance evidence  
• approval workflows  
• tenant isolation


Strategic principle:

TrustLayer wins when the architecture becomes:

AI Systems → TrustLayer → Real‑World Execution