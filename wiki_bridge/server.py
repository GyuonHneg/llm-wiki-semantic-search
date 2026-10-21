"""Bridge 服务：/healthz（免认证）· /search · /ask · /get（Bearer token）。

token 只在运行时注入，不写进代码、不写进日志。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .retriever import WikiIndex

app = FastAPI(title="LLM Wiki Bridge", version="0.1.0")

INDEX_PATH = Path(os.getenv("WIKI_INDEX", ".cache/wiki.pkl"))
_index: WikiIndex | None = None
bearer = HTTPBearer(auto_error=False)


def get_index() -> WikiIndex:
    global _index
    if _index is None:
        if not INDEX_PATH.exists():
            raise HTTPException(503, f"索引不存在：{INDEX_PATH}，先跑 python -m wiki_bridge.indexer")
        _index = WikiIndex(INDEX_PATH, alpha=float(os.getenv("WIKI_ALPHA", "0.6")))
    return _index


def require_token(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    """固定时间比较，避免时序侧信道；token 缺失时服务直接拒绝启动。"""
    expected = os.getenv("WIKI_TOKEN")
    if not expected:
        raise HTTPException(503, "服务端未配置 WIKI_TOKEN")
    if cred is None or not _equal(cred.credentials, expected):
        raise HTTPException(401, "unauthorized")
    return cred.credentials


def _equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)


class AskIn(BaseModel):
    question: str
    k: int = 5


@app.get("/healthz")
def healthz() -> dict:
    """免认证探活：只暴露统计信息，不暴露内容。"""
    if not INDEX_PATH.exists():
        return {"status": "no_index"}
    idx = get_index()
    return {"status": "ok", "pages": len(idx.pages), "vector": idx._vec is not None}


@app.get("/search")
def search(q: str, k: int = 5, _: str = Depends(require_token)) -> dict:
    t0 = time.time()
    idx = get_index()
    hits = idx.search(q, k=k)
    return {
        "query": q,
        "hits": [h.to_dict() for h in hits],
        "degraded": any(h.degraded for h in hits),   # 中文混合词常见：向量为空 → 退回全文检索
        "ms": int((time.time() - t0) * 1000),
    }


@app.get("/get")
def get(page: str, _: str = Depends(require_token)) -> dict:
    doc = get_index().get(page)
    if not doc:
        raise HTTPException(404, "page not found")
    return doc


@app.post("/ask")
def ask(payload: AskIn, _: str = Depends(require_token)) -> dict:
    """检索 + 生成：答案强制附 source_pages，方便人工核对、抑制幻觉。"""
    idx = get_index()
    hits = idx.search(payload.question, k=payload.k)
    context = "\n\n".join(f"[{i + 1}] {h.title} ({h.path})\n{h.snippet}" for i, h in enumerate(hits))

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return {"answer": "", "source_pages": [h.path for h in hits], "note": "未配置 DASHSCOPE_API_KEY，仅返回检索结果"}

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    resp = client.chat.completions.create(
        model=os.getenv("WIKI_MODEL", "qwen-plus"),
        messages=[
            {
                "role": "system",
                "content": "只依据给定资料回答，无法回答就说『资料中未提及』。结尾用 [n] 标注引用来源。",
            },
            {"role": "user", "content": f"【资料】\n{context}\n\n【问题】{payload.question}"},
        ],
        temperature=0.2,
    )
    return {
        "answer": resp.choices[0].message.content,
        "source_pages": [h.path for h in hits],
        "degraded": any(h.degraded for h in hits),
    }
