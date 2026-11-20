---
name: ai-strategy-extension
description: >-
  Use this skill when modifying AI algorithms in ai-tutor-service, switching LLM
  providers (Local/OpenAI/Gemini), or plugging in V2 modules (Harness evaluation,
  deep personalization engine, or RSI self-improving agent).
---

# AI Strategy Extension Skill

`ai-tutor-service` uses the **Strategy Pattern + Feature Flags** so V1 baseline implementations remain stable while V2 research modules plug in cleanly.

## 1. Locate the Strategy Protocol

Inspect [interfaces.py](../../../ai-tutor-service/app/strategies/interfaces.py) for the five core protocols:
- `ChapterizerStrategy`: ASR transcript segmentation, chapter summary, and second-level timestamp alignment.
- `KnowledgeRagStrategy`: Course-isolated chunking, indexing, retrieval, citation formatting, and policy guardrails.
- `AssessmentGeneratorStrategy`: Structured JSON question generation and weak-point personalized quiz assembly.
- `RecommenderStrategy`: Stateless learning resource ranking from platform-supplied learner history.
- `QuestionClusterStrategy`: Student question hotspot clustering for teaching blind-spot discovery.

## 2. Add a New Strategy Implementation

1. Create a new module under `ai-tutor-service/app/strategies/v2_<direction>/`.
2. Implement the protocol signature without importing anything from `platform-backend`.
3. Register the strategy in [registry.py](../../../ai-tutor-service/app/strategies/registry.py) gated by `runtime_config.feature_flags` (`enable_v2_harness`, `enable_v2_personalization`, `enable_v2_rsi`).
4. Ensure graceful fallback to `v1_default` on provider timeout or exception, setting `"degraded": true` in the response payload.

## 3. Validate

Run `pytest tests/test_ai_tutor_service.py -q` to verify contract compliance and fallback behavior.
