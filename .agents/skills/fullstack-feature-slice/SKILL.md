---
name: fullstack-feature-slice
description: >-
  Use this skill when adding or modifying end-to-end product features across
  platform-backend (models, services, API routes) and platform-frontend (Vue 3
  views, components, Pinia stores).
---

# Full-Stack Feature Slice Skill

Follow this clean layered slice order so human engineers can trace every feature predictably:

## 1. Backend Slice (`platform-backend/app/`)

1. **Entity & Enum Layer** (`models/<domain>.py`, `core/enums.py`): Add or update SQLAlchemy 2.0 mapped columns and `StrEnum` status constants (re-exported via `models/entities.py`).
2. **DTO Schema Layer** (`schemas/<domain>.py`): Define Pydantic v2 request/response DTOs separate from route handlers.
3. **ACL Client** (`clients/ai_service_client.py`): If the feature invokes AI capabilities, add a typed client method using `self._request(...)` with `X-Request-ID` propagation and structured warning fallback.
4. **Service Layer** (`services/*.py`): Implement domain logic, batch SQL queries (avoid N+1 loops), RBAC ownership checks, and `record_audit(...)` logging here — keep FastAPI route handlers thin.
5. **Router Layer** (`api/v1/*.py`): Declare clean HTTP endpoints delegating directly to services and wrapping responses with `ok_response(...)`.

## 2. Frontend Slice (`platform-frontend/src/`)

1. **Domain API Module** (`api/modules/<domain>.js`): Centralize endpoint paths here rather than hardcoding URLs in `.vue` files.
2. **Panel / View** (`views/<role>/panels/*.vue` or `components/*.vue`): Keep each single-file component focused on one sub-domain (~80–150 lines) using Vue 3 `<script setup>` and CSS variables from `styles/design-tokens.css`.

## 3. End-to-End Verification

1. Run backend & E2E tests: `pytest tests/test_platform_e2e.py -q`
2. Build frontend production bundle to ensure zero syntax/import errors: `pnpm --dir platform-frontend build`
