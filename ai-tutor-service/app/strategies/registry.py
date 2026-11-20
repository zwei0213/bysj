"""
策略注册与动态分发中心 (Strategy Registry)。
- V1 阶段默认返回 `v1_default` 下的五个标准实现；
- V2 演进时，开发同学只需在 `strategies/v2_*` 下新增实现类，并在此根据 `runtime_config.feature_flags`
  （`enable_v2_harness` / `enable_v2_personalization` / `enable_v2_rsi`）切换返回实例，业务路由层零改动。
"""
from app.services.telemetry_store import telemetry_store
from app.strategies.interfaces import (
    AssessmentGeneratorStrategy,
    ChapterizerStrategy,
    KnowledgeRagStrategy,
    QuestionClusterStrategy,
    RecommenderStrategy,
)
from app.strategies.v1_default.chapterizer import V1SemanticChapterizer
from app.strategies.v1_default.question_clusterer import V1QuestionHotspotClusterer
from app.strategies.v1_default.quiz_generator import V1KnowledgeQuizGenerator
from app.strategies.v1_default.rag_engine import V1CourseRagEngine
from app.strategies.v1_default.recommender import V1RuleSimilarityRecommender


class StrategyRegistry:
    def __init__(self) -> None:
        self._v1_chapterizer = V1SemanticChapterizer()
        self._v1_rag = V1CourseRagEngine()
        self._v1_quiz = V1KnowledgeQuizGenerator()
        self._v1_recommender = V1RuleSimilarityRecommender()
        self._v1_clusterer = V1QuestionHotspotClusterer()

    def get_chapterizer(self) -> ChapterizerStrategy:
        return self._v1_chapterizer

    def get_rag_engine(self) -> KnowledgeRagStrategy:
        # V2 扩展挂载点：若 flags.get("enable_v2_rsi") 为 True，可返回 V2RsiAgentRagEngine()
        return self._v1_rag

    def get_assessment_generator(self) -> AssessmentGeneratorStrategy:
        # V2 扩展挂载点：若 flags.get("enable_v2_personalization") 为 True，可返回 V2KnowledgeTracingQuizEngine()
        return self._v1_quiz

    def get_recommender(self) -> RecommenderStrategy:
        # V2 扩展挂载点：若 flags.get("enable_v2_personalization") 为 True，可返回 V2ForgettingCurveRecommender()
        return self._v1_recommender

    def get_question_clusterer(self) -> QuestionClusterStrategy:
        return self._v1_clusterer

    def describe_active_strategies(self) -> dict[str, str]:
        flags = telemetry_store.get_runtime_config().get("feature_flags", {})
        return {
            "chapterizer": self.get_chapterizer().strategy_name,
            "rag_engine": self.get_rag_engine().strategy_name,
            "assessment_generator": self.get_assessment_generator().strategy_name,
            "recommender": self.get_recommender().strategy_name,
            "question_clusterer": self.get_question_clusterer().strategy_name,
            "v2_flags_snapshot": str(flags),
        }


strategy_registry = StrategyRegistry()
