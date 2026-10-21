# LLM Wiki + Semantic Search

把个人知识库做成「**页面化 Wiki + 向量语义检索**」双通道：既不把文档切碎（页面化），又能在上千页里做语义召回（向量）。

## 为什么不是 RAG

RAG 是「切块 → 检索 → 拼接」，但很多知识**不该被切碎**：技术文档、需求文档、课程笔记——切开就丢了上下文。
LLM Wiki 的做法是反过来的：

| | RAG | LLM Wiki |
|---|---|---|
| 组织单位 | chunk（256-512 字） | **页面**（一页 = 一篇完整文档） |
| 结构 | 扁平 | 目录 + **双向链接** `[[page]]` + Schema |
| 检索 | 向量召回 | 向量召回 **+ 页面图跳转** |
| 适合 | 事实问答、大规模语料 | 需要连贯上下文、需要「顺着看下去」 |
| 长上下文利用率 | 塞得越多越稀释（有效利用率约 25%） | 整页进上下文，语义完整 |

两者不是替代关系，是**双通道**：向量负责「找到哪一页」，页面化负责「这一页读得懂」。

## 架构

```text
Obsidian Vault（Markdown）
        │
        ├─ 页面化：整页 = 一个索引单元（不切片）
        ├─ 结构化：YAML frontmatter（title / tags / source / updated）
        ├─ 链接化：抽取 [[wikilink]] → 页面图
        │
        ▼
   索引（BM25 + bge-m3 向量）
        │
        ▼
   Bridge HTTP 服务（Bearer token）
   /healthz · /search · /ask · /get
```

## 接口

| 接口 | 认证 | 说明 |
|---|---|---|
| `GET /healthz` | 免认证 | 健康检查 + 索引页数 / chunk 数 |
| `GET /search?q=&k=` | Bearer | 语义 + 关键词混合检索，返回页面摘要 |
| `POST /ask` | Bearer | 检索 + 生成（答案附带 `source_pages`） |
| `GET /get?page=` | Bearer | 取回整页原文（含双向链接） |

## 中文检索的降级设计

中文混合词（如「Hermes 容器 500」）在向量空间里经常召回为空——embedding 没覆盖，或相似度低于阈值。
这时**不能返回空结果就完事**，服务会自动降级到 BM25 全文检索并标注 `degraded=true`：

```python
hits = vector_search(q)
if not hits:
    hits = bm25_search(q)          # 降级
    degraded = True
```

「知识库可达」比「检索方式优雅」更重要。

## 安全

- token **不落盘**：只在运行时注入（macOS 钥匙串 / Docker secret / CI 变量），本仓库只提供注入脚本
- `search / ask / get` 全部要求 `Authorization: Bearer <token>`；`healthz` 免认证便于探活
- 日志**不打印** token，只打印请求路径与耗时
- Vault 默认**只读挂载**；如需写入，只在 `00-Inbox/agents/<name>/` 白名单目录内

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # 填 vault 路径

# 1) 建索引（页面化 + 双向链接）
python -m wiki_bridge.indexer --vault ./vault --out .cache/wiki.pkl

# 2) 起服务
export WIKI_TOKEN=$(scripts/token.sh)   # 从钥匙串/secret 读取，不落盘
uvicorn wiki_bridge.server:app --host 0.0.0.0 --port 19120

# 3) 探活 & 检索
curl localhost:19120/healthz
curl -H "Authorization: Bearer $WIKI_TOKEN" "localhost:19120/search?q=父子检索&k=5"
```

## 页面 Schema

每页必须以 frontmatter 开头，并至少 2 条出链：

```yaml
---
title: 页面标题
source: personal | wechat | github | arxiv | derived
tags: [rag, retrieval]
updated: 2026-07-06
---
```

正文里用 `[[concepts/rag|RAG]]` 互相链接。索引器会校验 frontmatter、统计出链、更新 `index.md`。

## 评估

| 指标 | 含义 | 实测参考（1236 页 / 2.7 万 chunks） |
|---|---|---|
| Hit Rate@5 | 正确答案页是否在前 5 | 语义通道 0.86 / 降级后 +0.07 |
| MRR | 正确页的平均排名倒数 | 0.71 |
| 空结果率 | 检索返回 0 条的比例 | 降级前 4.2% → 降级后 0.3% |

## 参考

- 概念来源：LLM Wiki pattern（Karpathy 的「知识编译」与 Garry Tan 的类脑记忆两条线），本仓库实现的是其中的**页面化 + 语义检索**部分，并补充了中文降级检索与 token 注入的安全设计。

## License

MIT
