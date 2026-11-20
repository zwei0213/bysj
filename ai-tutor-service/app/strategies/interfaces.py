from typing import Any, Protocol


class ChapterizerStrategy(Protocol):
    """视频章节化策略接口（ASR 转写 -> 语义切分 -> 标题与提要 -> 秒级时间戳对齐 -> 单章节重生成）。"""

    strategy_name: str

    async def chapterize(self, *, course_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def regenerate_single_chapter(self, *, course_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class KnowledgeRagStrategy(Protocol):
    """课程级隔离 RAG 助教问答策略接口（V2 可升级为方向 C 多步推理 RSI 智能体）。"""

    strategy_name: str

    async def generate_reply(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AssessmentGeneratorStrategy(Protocol):
    """智能出题与个性化薄弱点组题策略接口（V2 可升级为方向 B 知识追踪自适应组题）。"""

    strategy_name: str

    async def generate_questions(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class RecommenderStrategy(Protocol):
    """学习内容推荐策略接口（V1 规则+相似度；V2 升级为深度学习路径与遗忘曲线引擎）。"""

    strategy_name: str

    async def recommend(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class QuestionClusterStrategy(Protocol):
    """学情看板高频提问盲区聚类策略接口。"""

    strategy_name: str

    async def cluster_questions(self, payload: dict[str, Any]) -> dict[str, Any]: ...
