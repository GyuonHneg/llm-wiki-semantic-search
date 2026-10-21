"""混合检索 + 降级：向量优先，为空则退回 BM25 全文检索。"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Hit:
    path: str
    title: str
    score: float
    snippet: str
    degraded: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "title": self.title,
            "score": round(self.score, 4),
            "snippet": self.snippet,
            "degraded": self.degraded,
        }


class WikiIndex:
    def __init__(self, path: Path, alpha: float = 0.6) -> None:
        data = pickle.loads(Path(path).read_bytes())
        self.pages = data["pages"]
        self.bm25 = data["bm25"]
        self.backlinks = data.get("backlinks", {})
        self.alpha = alpha           # 向量权重；BM25 权重 = 1 - alpha
        self._vec = None
        self._model = None

    # ---------- 向量通道（可选，模型缺失时静默关闭）----------
    def _ensure_vector(self) -> bool:
        if self._vec is not None:
            return True
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return False
        try:
            self._model = SentenceTransformer("BAAI/bge-m3")
            texts = [f"{p['title']}\n{p['body'][:2000]}" for p in self.pages]
            self._vec = self._model.encode(texts, normalize_embeddings=True)
            return True
        except Exception:
            return False

    def _vector_search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self._ensure_vector():
            return []
        qv = self._model.encode([query], normalize_embeddings=True)[0]
        sims = self._vec @ qv
        idx = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in idx if sims[i] > 0.25]

    # ---------- 关键词通道 ----------
    def _bm25_search(self, query: str, k: int) -> list[tuple[int, float]]:
        if self.bm25 is None:
            return []
        from .indexer import tokenize

        scores = self.bm25.get_scores(tokenize(query))
        pairs = [(int(i), float(scores[i])) for i in np.argsort(-scores)[:k]]
        # BM25 在小语料上 IDF 可能为负（词出现在所有文档里），不能一刀切地过滤 <=0
        if any(s > 0 for _, s in pairs):
            pairs = [(i, s) for i, s in pairs if s > 0]
        return pairs

    def search(self, query: str, k: int = 5) -> list[Hit]:
        vec = self._vector_search(query, k * 2)
        bm = self._bm25_search(query, k * 2)

        if not vec and not bm:
            return []
        if not vec:
            return [self._mk(i, s, True) for i, s in bm[:k]]     # 降级：向量不可用
        if not bm:
            return [self._mk(i, s, False) for i, s in vec[:k]]

        # 各通道先做 min-max 归一化（两路量纲不同），再按 alpha 加权合并
        merged: dict[int, float] = {}
        for i, s in vec:
            merged[i] = merged.get(i, 0.0) + self.alpha * _minmax(s, [v for _, v in vec])
        for i, s in bm:
            merged[i] = merged.get(i, 0.0) + (1 - self.alpha) * _minmax(s, [v for _, v in bm])
        top = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:k]
        return [self._mk(i, s, False) for i, s in top]

    def _mk(self, idx: int, score: float, degraded: bool) -> Hit:
        p = self.pages[idx]
        body = p["body"].replace("\n", " ")
        return Hit(
            path=p["path"],
            title=p["title"],
            score=score,
            snippet=body[:160] + ("…" if len(body) > 160 else ""),
            degraded=degraded,
        )

    def get(self, path: str) -> dict | None:
        for p in self.pages:
            if p["path"] == path or p["path"].endswith(path):
                return {**p, "backlinks": self.backlinks.get(Path(p["path"]).stem, [])}
        return None


def _minmax(x: float, pool: list[float]) -> float:
    """把某个通道的分数拉到 0-1，避免向量余弦与 BM25 量纲不一致导致一边倒。"""
    lo, hi = min(pool), max(pool)
    return (x - lo) / (hi - lo) if hi > lo else 1.0
