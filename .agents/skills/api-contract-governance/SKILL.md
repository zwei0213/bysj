---
name: api-contract-governance
description: >-
  Use this skill when designing, modifying, or reviewing HTTP APIs between
  platform-frontend, platform-backend, and ai-tutor-service, or updating OpenAPI
  contracts in contracts/.
---

# API Contract Governance Skill

Follow this workflow whenever adding or changing any endpoint across the platform or AI service.

## 1. Contract-First Checklist

1. **Update OpenAPI First**: Edit [ai-tutor-service.v1.openapi.yaml](../../../contracts/ai-tutor-service.v1.openapi.yaml) or [platform-backend.v1.openapi.yaml](../../../contracts/platform-backend.v1.openapi.yaml) before changing router code.
2. **Resource-Oriented URL Rules**:
   - Use kebab-case plural nouns (`/api/v1/quiz-sessions`, `/v1/ops/feedback-samples`).
   - Model async triggers or state commands as sub-resource creations (`POST /v1/ops/jobs/{job_id}/retries`, `POST /v1/ops/knowledge-bases/{course_id}/rebuilds`) returning `202 Accepted`.
3. **Auth Channel Separation**:
   - End-user endpoints (`/api/v1/*`): `Authorization: Bearer <jwt>` + role dependency (`require_roles`).
   - Business AI endpoints (`/v1/*`): `X-Service-Key` header.
   - AI Ops/Governance endpoints (`/v1/ops/*`): `X-Ops-Key` header (strictly separated from business key).
   - Webhook callbacks (`/internal/v1/webhooks/*`): `X-Webhook-Signature` (HMAC-SHA256) + `Idempotency-Key`.
4. **Backward Compatibility**:
   - Never delete or rename existing JSON fields in `/v1`; add optional fields with defaults or introduce `/v2` routes.

## 2. Verification

Run the contract and integration test suite after making API updates:
- `pytest tests/ -q`
