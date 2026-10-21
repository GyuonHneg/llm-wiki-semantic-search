"""页面化索引：不切片，一页 = 一个索引单元；抽取 frontmatter 与 wikilink 建页面图。"""

from __future__ import annotations

import argparse
import json
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi

_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TAG_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.M)


@dataclass
class Page:
    path: str
    title: str
    tags: list[str] = field(default_factory=list)
    source: str = ""
    updated: str = ""
    body: str = ""
    outlinks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "title": self.title,
            "tags": self.tags,
            "source": self.source,
            "updated": self.updated,
            "outlinks": self.outlinks,
            "body": self.body,
        }


def parse_page(path: Path, root: Path) -> Page | None:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    m = _FRONT_RE.match(raw)
    meta, body = ("", raw)
    if m:
        meta, body = m.group(1), raw[m.end() :]

    def field(name: str, default: str = "") -> str:
        mm = re.search(rf"^{name}:\s*(.+)$", meta, re.M)
        return mm.group(1).strip().strip("\"'") if mm else default

    tags_m = _TAG_RE.search(meta)
    tags = [t.strip() for t in tags_m.group(1).split(",")] if tags_m else []

    title = field("title") or path.stem
    links = []
    for lm in _LINK_RE.finditer(body):
        target = lm.group(1).strip()
        links.append(target if "/" in target else f"{path.stem}")

    return Page(
        path=str(path.relative_to(root)),
        title=title,
        tags=tags,
        source=field("source"),
        updated=field("updated"),
        body=body.strip(),
        outlinks=sorted(set(links)),
    )


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.lower())


def build_index(vault: Path, out: Path) -> dict:
    pages: list[Page] = []
    for p in sorted(vault.rglob("*.md")):
        if any(part.startswith(".") for part in p.parts):
            continue
        page = parse_page(p, vault)
        if page:
            pages.append(page)

    corpus = [tokenize(f"{p.title}\n{p.body}") for p in pages]
    payload = {
        "vault": str(vault),
        "pages": [p.to_dict() for p in pages],
        "bm25": BM25Okapi(corpus) if corpus else None,
        "backlinks": _backlinks(pages),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pickle.dumps(payload))

    stats = {
        "pages": len(pages),
        "links": sum(len(p.outlinks) for p in pages),
        "orphans": sum(1 for p in pages if not p.outlinks),
        "index": str(out),
    }
    return stats


def _backlinks(pages: list[Page]) -> dict[str, list[str]]:
    """反向链接：页面图能「顺着看下去」，这是 Wiki 相对扁平 chunk 的核心优势。"""
    back: dict[str, list[str]] = {}
    for p in pages:
        for t in p.outlinks:
            back.setdefault(Path(t).stem, []).append(p.path)
    return back


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--out", default=".cache/wiki.pkl")
    args = ap.parse_args()
    print(json.dumps(build_index(Path(args.vault), Path(args.out)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
