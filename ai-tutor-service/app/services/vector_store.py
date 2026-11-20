"""
课程隔离混合知识库存储（官方 `qdrant-client` 嵌入式本地/远端双模 + SQLite WAL 文档元数据）。
特性：
1. 默认零容器运行 `QdrantClient(path=...)` 嵌入式本地持久化；配置 `QDRANT_URL` 时无缝切换外部 Qdrant 集群；
2. 写入、删除、重建与查询 100% 由 Qdrant 原生执行（Dense 语义向量 + Sparse 词法向量双路混合召回 + `course_id` Payload 严格隔离）；
3. 支持带滑动重叠 (`overlap`) 的段落感知分块，避免跨句语义截断；
4. SQLite 仅保存课程索引状态机与原始文档备份（用于全量重建），全部通过 `closing()` 安全释放连接，杜绝 FD 泄漏。
"""
import atexit
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid
from qdrant_client import QdrantClient, models
from app.core.config import settings
from app.core.schemas import utc_now_iso
from app.providers.embedding_provider import embedding_client

logger = logging.getLogger(__name__)

_CJK_STOP_CHARS = set("的了在是我有和就不人都一上也很到说要去你会着没看好这那吗呢吧啊请问么什如何怎样为能可中与及或把被让给对从向")
_SPARSE_VOCAB_MOD = 2_000_003

# 进程级 QdrantClient 复用池（避免同一本地存储目录被重复加文件锁冲突）
_QDRANT_CLIENTS: dict[str, QdrantClient] = {}
_QDRANT_LOCK = threading.RLock()


def _close_all_qdrant_clients() -> None:
    with _QDRANT_LOCK:
        for client in _QDRANT_CLIENTS.values():
            try:
                client.close()
            except Exception:
                pass
        _QDRANT_CLIENTS.clear()


atexit.register(_close_all_qdrant_clients)


def _tokenize_mixed(text: str) -> list[str]:
    lowered = text.lower()
    ascii_words = re.findall(r"[a-z0-9_+#.-]{2,}", lowered)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    meaningful_unigrams = [ch for ch in cjk_chars if ch not in _CJK_STOP_CHARS]
    return ascii_words + cjk_bigrams + meaningful_unigrams


def _build_sparse_vector(text: str) -> models.SparseVector:
    """将中英文混合词元转换为 L2 归一化 Qdrant SparseVector（点积即词法余弦相似度 ∈ [0, 1]）。"""
    counts = Counter(_tokenize_mixed(text))
    if not counts:
        return models.SparseVector(indices=[], values=[])

    # 亚线性 TF 加权 (1 + log(tf)) 缓解长文高频词过度主导；英文专业术语与中文双字词组给予更高权重
    bucket_weights: dict[int, float] = {}
    for tok, cnt in counts.items():
        idx = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=4).digest(), "little") % _SPARSE_VOCAB_MOD
        term_boost = 2.0 if tok.isascii() else (1.3 if len(tok) >= 2 else 0.55)
        weight = (1.0 + math.log(cnt)) * term_boost
        bucket_weights[idx] = bucket_weights.get(idx, 0.0) + weight

    norm = math.sqrt(sum(v * v for v in bucket_weights.values())) or 1.0
    sorted_items = sorted(bucket_weights.items())
    return models.SparseVector(
        indices=[k for k, _ in sorted_items],
        values=[round(v / norm, 6) for _, v in sorted_items],
    )


def _split_chunks(text: str, max_chars: int = 380, overlap: int = 60) -> list[str]:
    """段落感知 + 滑动重叠切块，兼顾 Markdown 标题完整性和跨段上下文连贯性。"""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}|\r\n\r\n|(?<=[。！？!?])\s+", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 1 <= max_chars:
            buf = f"{buf}\n{para}".strip() if buf else para
        else:
            if buf:
                chunks.append(buf)
                tail = buf[-overlap:] if overlap > 0 and len(buf) > overlap else ""
            else:
                tail = ""
            if len(para) <= max_chars:
                buf = f"{tail}\n{para}".strip() if tail and len(tail) + len(para) + 1 <= max_chars else para
            else:
                step = max(1, max_chars - overlap)
                for idx in range(0, len(para), step):
                    piece = para[idx : idx + max_chars].strip()
                    if piece:
                        chunks.append(piece)
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks or [text[:max_chars]]


def _chunk_id_to_uuid(chunk_id: str) -> str:
    return str(uuid.UUID(hashlib.md5(chunk_id.encode("utf-8")).hexdigest()))


class CourseKnowledgeStore:
    def __init__(self, storage_dir: str | None = None) -> None:
        self._override_dir = storage_dir
        self._lock = threading.RLock()
        self._initialized_db_path: str | None = None
        self._ensured_collections: set[tuple[str, str]] = set()

    def _base_dir(self) -> Path:
        base = Path(self._override_dir or settings.data_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _qdrant_target_key(self) -> tuple[str, str, str]:
        url = (os.environ.get("QDRANT_URL") or settings.qdrant_url or "").strip().rstrip("/")
        api_key = (os.environ.get("QDRANT_API_KEY") or settings.qdrant_api_key or "").strip()
        if url:
            return ("remote", url, api_key)
        local_path = str((self._base_dir() / "qdrant_data").resolve())
        return ("local", local_path, "")

    def _get_qdrant(self) -> tuple[QdrantClient, str]:
        mode, target, api_key = self._qdrant_target_key()
        cache_key = f"{mode}:{target}"
        with _QDRANT_LOCK:
            client = _QDRANT_CLIENTS.get(cache_key)
            if client is None:
                if mode == "remote":
                    client = QdrantClient(url=target, api_key=api_key or None, timeout=15.0)
                else:
                    Path(target).mkdir(parents=True, exist_ok=True)
                    client = QdrantClient(path=target)
                _QDRANT_CLIENTS[cache_key] = client
            return client, cache_key

    def _collection_name(self, dim: int) -> str:
        return f"{settings.qdrant_collection_prefix}_{dim}d"

    def _ensure_collection(self, client: QdrantClient, cache_key: str, dim: int) -> str:
        name = self._collection_name(dim)
        col_key = (cache_key, name)
        if col_key in self._ensured_collections:
            return name
        with _QDRANT_LOCK:
            if not client.collection_exists(name):
                client.create_collection(
                    collection_name=name,
                    vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
                    sparse_vectors_config={"sparse": models.SparseVectorParams()},
                )
            self._ensured_collections.add(col_key)
        return name

    def _list_existing_collections(self, client: QdrantClient) -> list[str]:
        prefix = f"{settings.qdrant_collection_prefix}_"
        return [c.name for c in client.get_collections().collections if c.name.startswith(prefix)]

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        db_file = self._base_dir() / "knowledge_index.db"
        conn = sqlite3.connect(str(db_file), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        with closing(conn):
            if self._initialized_db_path != str(db_file):
                with self._lock:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS kb_courses (
                            course_id TEXT PRIMARY KEY,
                            status TEXT NOT NULL DEFAULT 'unindexed',
                            active_dim INTEGER NOT NULL DEFAULT 64,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            last_built_at TEXT,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS kb_documents (
                            course_id TEXT NOT NULL,
                            document_id TEXT NOT NULL,
                            title TEXT NOT NULL,
                            source_type TEXT NOT NULL,
                            content TEXT NOT NULL,
                            metadata_json TEXT NOT NULL,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            updated_at TEXT NOT NULL,
                            PRIMARY KEY (course_id, document_id)
                        );
                        """
                    )
                    conn.commit()
                    self._initialized_db_path = str(db_file)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _delete_qdrant_points(self, *, course_id: str, document_id: str | None = None) -> None:
        client, _ = self._get_qdrant()
        must_conds = [models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))]
        if document_id is not None:
            must_conds.append(models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)))
        flt = models.Filter(must=must_conds)
        for col in self._list_existing_collections(client):
            client.delete(collection_name=col, points_selector=models.FilterSelector(filter=flt), wait=True)

    def _upsert_qdrant_chunks(
        self,
        *,
        course_id: str,
        document_id: str,
        title: str,
        source_type: str,
        content: str,
        metadata: dict[str, Any],
        prebuilt_vectors: list[list[float]] | None = None,
    ) -> tuple[int, int]:
        self._delete_qdrant_points(course_id=course_id, document_id=document_id)
        raw_chunks = _split_chunks(content)
        dense_vectors = prebuilt_vectors or embedding_client.embed_texts_sync([f"{title}\n{p}" for p in raw_chunks])
        dim = len(dense_vectors[0]) if dense_vectors and dense_vectors[0] else 64

        client, cache_key = self._get_qdrant()
        collection = self._ensure_collection(client, cache_key, dim)

        points: list[models.PointStruct] = []
        for idx, piece in enumerate(raw_chunks):
            chunk_id = f"{course_id}::{document_id}#c{idx + 1}"
            dense_vec = dense_vectors[idx] if idx < len(dense_vectors) else [0.0] * dim
            sparse_vec = _build_sparse_vector(f"{title} {piece}")
            points.append(
                models.PointStruct(
                    id=_chunk_id_to_uuid(chunk_id),
                    vector={"dense": dense_vec, "sparse": sparse_vec},
                    payload={
                        "chunk_id": chunk_id,
                        "course_id": course_id,
                        "document_id": document_id,
                        "title": title,
                        "source_type": source_type,
                        "snippet": piece,
                        "metadata": metadata,
                    },
                )
            )
        if points:
            client.upsert(collection_name=collection, points=points, wait=True)
        return len(points), dim

    def _set_course_status(
        self,
        conn: sqlite3.Connection,
        course_id: str,
        status: str,
        *,
        active_dim: int | None = None,
        chunk_count: int | None = None,
        last_built_at: str | None = None,
    ) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO kb_courses (course_id, status, active_dim, chunk_count, last_built_at, updated_at)
            VALUES (?, ?, COALESCE(?, 64), COALESCE(?, 0), ?, ?)
            ON CONFLICT(course_id) DO UPDATE SET
                status = excluded.status,
                active_dim = COALESCE(?, kb_courses.active_dim),
                chunk_count = COALESCE(?, kb_courses.chunk_count),
                last_built_at = COALESCE(excluded.last_built_at, kb_courses.last_built_at),
                updated_at = excluded.updated_at
            """,
            (course_id, status, active_dim, chunk_count, last_built_at, now, active_dim, chunk_count),
        )

    def _refresh_course_meta(self, conn: sqlite3.Connection, course_id: str, *, active_dim: int | None = None) -> tuple[str, int, str | None]:
        total_chunks = int(
            conn.execute("SELECT COALESCE(SUM(chunk_count), 0) FROM kb_documents WHERE course_id = ?", (course_id,)).fetchone()[0]
        )
        status = "ready" if total_chunks > 0 else "unindexed"
        built_at = utc_now_iso() if total_chunks > 0 else None
        self._set_course_status(conn, course_id, status, active_dim=active_dim, chunk_count=total_chunks, last_built_at=built_at)
        return status, total_chunks, built_at

    def upsert_document(
        self,
        course_id: str,
        document_id: str,
        *,
        title: str,
        source_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        prebuilt_vectors: list[list[float]] | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        now = utc_now_iso()
        with self._lock:
            doc_chunk_count, dim = self._upsert_qdrant_chunks(
                course_id=course_id,
                document_id=document_id,
                title=title,
                source_type=source_type,
                content=content,
                metadata=meta,
                prebuilt_vectors=prebuilt_vectors,
            )
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO kb_documents (course_id, document_id, title, source_type, content, metadata_json, chunk_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(course_id, document_id) DO UPDATE SET
                        title = excluded.title,
                        source_type = excluded.source_type,
                        content = excluded.content,
                        metadata_json = excluded.metadata_json,
                        chunk_count = excluded.chunk_count,
                        updated_at = excluded.updated_at
                    """,
                    (course_id, document_id, title, source_type, content, json.dumps(meta, ensure_ascii=False), doc_chunk_count, now),
                )
                status, total_chunks, last_built_at = self._refresh_course_meta(conn, course_id, active_dim=dim)

        return {
            "course_id": course_id,
            "document_id": document_id,
            "status": status,
            "document_chunk_count": doc_chunk_count,
            "total_chunk_count": total_chunks,
            "last_built_at": last_built_at,
        }

    async def upsert_document_async(
        self,
        course_id: str,
        document_id: str,
        *,
        title: str,
        source_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_chunks = _split_chunks(content)
        vectors = await embedding_client.embed_texts_async([f"{title}\n{p}" for p in raw_chunks])
        return self.upsert_document(
            course_id,
            document_id,
            title=title,
            source_type=source_type,
            content=content,
            metadata=metadata,
            prebuilt_vectors=vectors,
        )

    def delete_document(self, course_id: str, document_id: str) -> dict[str, Any]:
        with self._lock:
            self._delete_qdrant_points(course_id=course_id, document_id=document_id)
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM kb_documents WHERE course_id = ? AND document_id = ?",
                    (course_id, document_id),
                )
                status, total_chunks, _ = self._refresh_course_meta(conn, course_id)
        return {
            "course_id": course_id,
            "document_id": document_id,
            "removed": cur.rowcount > 0,
            "status": status,
            "total_chunk_count": total_chunks,
        }

    def _apply_rebuild(
        self,
        course_id: str,
        docs: list[dict[str, Any]],
        doc_vectors: dict[str, list[list[float]]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._delete_qdrant_points(course_id=course_id)
            active_dim = 64
            with self._conn() as conn:
                for doc in docs:
                    cnt, dim = self._upsert_qdrant_chunks(
                        course_id=course_id,
                        document_id=doc["document_id"],
                        title=doc["title"],
                        source_type=doc["source_type"],
                        content=doc["content"],
                        metadata=json.loads(doc["metadata_json"] or "{}"),
                        prebuilt_vectors=(doc_vectors or {}).get(doc["document_id"]),
                    )
                    active_dim = dim
                    conn.execute(
                        "UPDATE kb_documents SET chunk_count = ? WHERE course_id = ? AND document_id = ?",
                        (cnt, course_id, doc["document_id"]),
                    )
                self._refresh_course_meta(conn, course_id, active_dim=active_dim)
                if not docs:
                    self._set_course_status(conn, course_id, "ready", active_dim=active_dim, chunk_count=0, last_built_at=utc_now_iso())
        return self.get_course_status(course_id)

    def _begin_rebuild(self, course_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            self._set_course_status(conn, course_id, "indexing")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT document_id, title, source_type, content, metadata_json FROM kb_documents WHERE course_id = ?",
                    (course_id,),
                ).fetchall()
            ]

    def rebuild_course_index(self, course_id: str) -> dict[str, Any]:
        """全量重建指定课程索引，严格维护 `indexing -> ready / failed` 状态流转。"""
        docs = self._begin_rebuild(course_id)
        try:
            return self._apply_rebuild(course_id, docs)
        except Exception:
            with self._lock, self._conn() as conn:
                self._set_course_status(conn, course_id, "failed")
            raise

    async def rebuild_course_index_async(self, course_id: str) -> dict[str, Any]:
        docs = self._begin_rebuild(course_id)
        try:
            doc_vectors = {
                doc["document_id"]: await embedding_client.embed_texts_async(
                    [f"{doc['title']}\n{p}" for p in _split_chunks(doc["content"])]
                )
                for doc in docs
            }
            return self._apply_rebuild(course_id, docs, doc_vectors=doc_vectors)
        except Exception:
            with self._lock, self._conn() as conn:
                self._set_course_status(conn, course_id, "failed")
            raise

    def search(
        self,
        course_id: str,
        query: str,
        *,
        top_k: int = 4,
        chapter_id: str | None = None,
        video_id: str | None = None,
        query_dense_override: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        sparse_q = _build_sparse_vector(query)
        if not sparse_q.indices:
            return []

        with self._conn() as conn:
            c_row = conn.execute("SELECT active_dim, chunk_count FROM kb_courses WHERE course_id = ?", (course_id,)).fetchone()
        if not c_row or int(c_row["chunk_count"]) <= 0:
            return []

        active_dim = int(c_row["active_dim"] or 64)
        client, _ = self._get_qdrant()
        collection = self._collection_name(active_dim)
        if not client.collection_exists(collection):
            return []

        query_dense = query_dense_override
        if query_dense is None:
            embs = embedding_client.embed_texts_sync([query])
            query_dense = embs[0] if embs else None
        if query_dense is not None and len(query_dense) != active_dim:
            query_dense = embedding_client.embed_local_hash([query], dim=active_dim)[0] if active_dim == 64 else None

        course_filter = models.Filter(
            must=[models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))]
        )
        recall_limit = max(12, top_k * 4)

        # 原生 Qdrant 双路召回（Dense 语义 + Sparse 词法），彻底避免全表拉取进 Python 内存
        candidates: dict[str, dict[str, Any]] = {}
        sparse_hits = client.query_points(
            collection_name=collection,
            query=sparse_q,
            using="sparse",
            query_filter=course_filter,
            limit=recall_limit,
            with_payload=True,
        ).points
        for pt in sparse_hits:
            pl = pt.payload or {}
            cid = str(pl.get("chunk_id") or pt.id)
            candidates[cid] = {"payload": pl, "lexical": max(0.0, float(pt.score)), "dense": 0.0}

        if query_dense:
            dense_hits = client.query_points(
                collection_name=collection,
                query=query_dense,
                using="dense",
                query_filter=course_filter,
                limit=recall_limit,
                with_payload=True,
            ).points
            for pt in dense_hits:
                pl = pt.payload or {}
                cid = str(pl.get("chunk_id") or pt.id)
                entry = candidates.setdefault(cid, {"payload": pl, "lexical": 0.0, "dense": 0.0})
                entry["dense"] = max(0.0, float(pt.score))

        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in candidates.values():
            # 短查询-长段落 L2 归一化稀疏余弦受长度比压缩，乘以校准系数并保持单文档/多文档分数稳定
            lexical_sim = min(0.98, entry["lexical"] * 2.1)
            dense_sim = min(0.98, entry["dense"])
            if active_dim > 64 and query_dense:
                sim = max(lexical_sim * 0.85, 0.58 * dense_sim + 0.42 * lexical_sim)
            else:
                sim = (
                    0.30 * dense_sim + 0.70 * lexical_sim
                    if lexical_sim < 0.08
                    else max(lexical_sim, 0.42 * dense_sim + 0.65 * lexical_sim)
                )

            pl = entry["payload"]
            meta = pl.get("metadata") or {}
            if sim > 0.05:
                if chapter_id and meta.get("chapter_id") == chapter_id:
                    sim += 0.14
                if video_id and meta.get("video_id") == video_id:
                    sim += 0.08
                if pl.get("source_type") == "teacher_qa":
                    sim += 0.12

            if sim > 0.02:
                scored.append((min(0.99, round(sim, 4)), pl))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "chunk_id": pl["chunk_id"],
                "source_id": pl["document_id"],
                "source_type": pl["source_type"],
                "title": pl["title"],
                "snippet": pl["snippet"],
                "score": score,
                "video_id": (pl.get("metadata") or {}).get("video_id"),
                "chapter_id": (pl.get("metadata") or {}).get("chapter_id"),
                "timestamp_sec": (pl.get("metadata") or {}).get("start_sec"),
            }
            for score, pl in scored[:top_k]
        ]

    async def search_async(
        self,
        course_id: str,
        query: str,
        *,
        top_k: int = 4,
        chapter_id: str | None = None,
        video_id: str | None = None,
    ) -> list[dict[str, Any]]:
        embs = await embedding_client.embed_texts_async([query])
        q_vec = embs[0] if embs else None
        return self.search(
            course_id,
            query,
            top_k=top_k,
            chapter_id=chapter_id,
            video_id=video_id,
            query_dense_override=q_vec,
        )

    def list_course_chunks(self, course_id: str) -> list[dict[str, Any]]:
        client, _ = self._get_qdrant()
        course_filter = models.Filter(
            must=[models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))]
        )
        chunks: list[dict[str, Any]] = []
        for col in self._list_existing_collections(client):
            points, _ = client.scroll(
                collection_name=col,
                scroll_filter=course_filter,
                limit=500,
                with_payload=True,
                with_vectors=False,
            )
            for pt in points:
                pl = pt.payload or {}
                chunks.append(
                    {
                        "chunk_id": pl.get("chunk_id", ""),
                        "document_id": pl.get("document_id", ""),
                        "source_id": pl.get("document_id", ""),
                        "title": pl.get("title", ""),
                        "source_type": pl.get("source_type", ""),
                        "snippet": pl.get("snippet", ""),
                        "metadata": pl.get("metadata") or {},
                    }
                )
        return chunks

    def get_course_status(self, course_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            c_row = conn.execute(
                "SELECT status, chunk_count, last_built_at, updated_at FROM kb_courses WHERE course_id = ?",
                (course_id,),
            ).fetchone()
            doc_cnt = int(
                conn.execute("SELECT COUNT(*) FROM kb_documents WHERE course_id = ?", (course_id,)).fetchone()[0]
            )
        chk_cnt = int(c_row["chunk_count"]) if c_row else 0
        return {
            "course_id": course_id,
            "status": c_row["status"] if c_row else ("ready" if chk_cnt > 0 else "unindexed"),
            "document_count": doc_cnt,
            "chunk_count": chk_cnt,
            "last_built_at": c_row["last_built_at"] if c_row else None,
            "updated_at": c_row["updated_at"] if c_row else utc_now_iso(),
        }

    def list_all_statuses(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            cids = [r[0] for r in conn.execute("SELECT course_id FROM kb_courses ORDER BY course_id").fetchall()]
        return [self.get_course_status(cid) for cid in cids]


knowledge_store = CourseKnowledgeStore()
