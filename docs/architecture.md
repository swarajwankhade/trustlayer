

# TrustLayer Architecture Overview

This document describes the architectural design of TrustLayer.

It provides context for developers and AI coding agents so implementation decisions align with the long‑term infrastructure direction.

TrustLayer is designed to evolve into a **control plane for AI execution governance**.

---

# 1. System Overview

TrustLayer sits between AI systems and real‑world execution systems.

Architecture pattern:

AI Systems → TrustLayer → Execution Systems

TrustLayer acts as a deterministic authorization boundary for probabilistic AI decisions.

Responsibilities:

• Validate AI action proposals  
• Evaluate policies  
• Track financial exposure  
• Detect abnormal behavior  
• Record immutable decisions  
• Provide operational safety controls

---

# 2. Core Components (MVP)

The MVP architecture consists of the following components.

## API Service

Technology: FastAPI

Responsibilities:

• Accept action authorization requests  
• Authenticate API callers  
• Validate payload schemas  
• Orchestrate decision evaluation

Endpoints:

POST /v1/actions/refund  
POST /v1/actions/credit

---

## Policy Engine

The policy engine evaluates proposed actions against deterministic rules.

Responsibilities:

• Enforce refund and credit limits  
• Enforce daily exposure caps  
• Detect near‑cap escalation conditions  
• Produce deterministic decisions

Decision outputs:

ALLOW  
ESCALATE  
BLOCK

Policy rules are versioned and stored in Postgres.

---

## Exposure Tracker

Exposure tracking ensures that AI automation cannot exceed defined financial limits.

Storage: Redis

Tracked metrics:

• Global daily financial exposure  
• Per‑user daily financial exposure  
• Per‑user daily action counts

Counters use UTC day buckets and expire automatically.

Exposure is updated only when decisions return ALLOW.

---

## Decision Log Store

Storage: Postgres

The decision log is an append‑only ledger that records every authorization decision.

Each record includes:

• action payload  
• decision outcome  
• policy version  
• exposure snapshot  
• timestamps

This ledger enables auditing, debugging, and deterministic replay.

---

## Kill Switch

A global kill switch allows operators to immediately disable AI automation.

When enabled:

All decisions automatically return ESCALATE.

This is used during incidents or unexpected automation behavior.

---

# 3. Decision Flow

The decision pipeline processes each AI action request.

Flow:

1. AI proposes action (refund or credit)
2. API validates request and authentication
3. Active policy version is loaded
4. Exposure context is retrieved from Redis
5. Policy engine evaluates request
6. Decision is produced
7. Decision event is stored in Postgres
8. Redis counters updated if decision = ALLOW
9. Decision returned to caller

---

# 4. Control Plane vs Decision Plane

TrustLayer now has a clearer internal split:

Control Plane

• Policy lifecycle (`create`, `validate`, `activate`)  
• Runtime controls (`kill switch`, `observe_only`)  
• Admin surfaces (dashboard, metrics, export)

Decision Plane

• Real-time action authorization (`/v1/actions/refund`, `/v1/actions/credit`)  
• Evaluator resolution by `policy_type`  
• Exposure retrieval / mutation in Redis  
• Immutable decision event writes

This is still one deployable service in MVP, but the internal boundaries are explicit.

---

# 5. Evaluator Registry Architecture

TrustLayer uses a typed evaluator registry keyed by `policy_type`.

```text
policy.policy_type -> get_evaluator(policy_type) -> evaluator implementation
```

Current registry entry:

• `refund_credit_v1`

Registry responsibilities:

• map `policy_type` to evaluator  
• fail fast for unsupported types  
• keep runtime behavior deterministic

---

# 6. Base Evaluator Contract

Each evaluator implementation follows the same contract:

```python
class Evaluator(Protocol):
    policy_type: str
    def validate_rules(self, rules_json: dict[str, Any]) -> Any: ...
    def normalize_action(self, action_type: str, payload: dict[str, Any]) -> Any: ...
    def evaluate(self, action: Any, exposure_context: Any, rules: Any) -> EvaluationResult: ...
```

Design intent:

• typed rules per `policy_type`  
• typed normalization per action family  
• pure deterministic evaluation output (`ALLOW` / `ESCALATE` / `BLOCK`, plus reason codes)

---

# 7. refund_credit_v1 Evaluator Structure

Current evaluator family layout:

```text
app/evaluators/refund_credit_v1/
  schema.py      # typed rules + typed exposure context
  normalizer.py  # maps refund/credit payloads into normalized action
  evaluator.py   # deterministic rule evaluation
```

Key behavior:

• supports `refund` and `credit_adjustment`  
• enforces per-action and daily caps  
• computes near-cap escalation  
• uses projected exposure values during evaluation

---

# 8. Decision Evaluation Pipeline (Current Runtime)

Live action runtime sequence:

```text
1) API auth + request validation
2) idempotency lookup by request_id
3) kill switch / rate-limit operational guards
4) active policy load (policy_type + rules_json)
5) evaluator resolve by policy_type
6) action normalization via evaluator
7) exposure fetch + typed conversion
8) evaluator.evaluate(...)
9) observe_only transform (if enabled)
10) decision_event append
11) exposure increment only when effective decision is ALLOW in enforce mode
```

Replay and simulation reuse the same evaluator resolution + typed evaluation path, but remain read-only.

---

# 9. Decision Event Evidence Model

`decision_events` are append-only evidence records. Important fields include:

• request identity (`event_id`, `request_id`, `timestamp`)  
• outcome (`decision`, `reason_codes`)  
• evaluator metadata (`policy_id`, `policy_version`, `policy_type`, `runtime_mode`)  
• observe-only evidence (`would_decision`, `would_reason_codes`)  
• replay inputs (`action_payload_json`, `exposure_snapshot_json`)

This shape supports:

• deterministic replay  
• post-incident investigation  
• policy and evaluator attribution

---

# 10. Simulation and Replay Architecture

Simulation (`POST /v1/admin/simulate`)

• resolves evaluator using explicit policy or active policy  
• defaults `policy_type` safely for legacy callers  
• normalizes payload and evaluates without writing events or mutating Redis

Replay (`POST /v1/admin/decisions/{event_id}/replay`)

• loads stored event + referenced policy version  
• resolves evaluator from stored `event.policy_type` (fallbacks for legacy rows)  
• reconstructs normalized action + exposure snapshot  
• re-evaluates read-only and compares with original/would decision

---

# 11. Storage Architecture

TrustLayer uses two primary storage systems.

## Postgres

Stores durable data:

• policies  
• decision_events  
• kill_switch

Postgres functions as the **source of truth** for policy and decision history.

---

## Redis

Stores fast counters for exposure tracking.

Characteristics:

• in‑memory performance  
• automatic expiration  
• atomic increments

Redis is used only for short‑lived operational state.

---

# 12. Failure Handling

TrustLayer is designed to fail safe.

Redis unavailable → ESCALATE

Postgres unavailable → fail safe or temporarily buffer logs

Policy unavailable → allow with diagnostic reason code

The system must never allow unsafe automation due to infrastructure failures.

---

# 13. Reliability Principles

TrustLayer must operate as safety infrastructure.

Key principles:

• deterministic decision behavior  
• idempotent request handling  
• append‑only audit logs  
• safe degradation under failure

---

# 14. Future Architecture Evolution

TrustLayer will evolve into a broader governance platform.

## Multi‑Action Governance

Future action domains may include:

• billing adjustments  
• subscription plan changes  
• discounts and promotions  
• account permissions

The policy engine must remain domain‑agnostic.

---

## Control Plane vs Data Plane

As TrustLayer scales, the architecture will split into:

Control Plane

• policy management  
• dashboards  
• analytics  
• tenant management

Data Plane

• real‑time decision engine  
• exposure tracking  
• enforcement logic

This separation allows independent scaling.

---

## Multi‑Tenant Architecture

Future deployments will support multiple organizations.

Design goals:

• tenant‑isolated data  
• tenant‑scoped API keys  
• tenant‑specific policies

---

# 15. Long‑Term Architecture Vision

TrustLayer becomes foundational infrastructure for AI execution.

Architecture model:

AI Systems → TrustLayer → Real‑World Systems

TrustLayer functions as:

• authorization layer  
• risk control engine  
• audit infrastructure  
• automation governance plane

The platform succeeds when TrustLayer becomes the mandatory checkpoint between AI intent and real‑world execution.
