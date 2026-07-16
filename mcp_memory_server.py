"""memory MCP server —— 本地全文检索 & 记忆存储。

不依赖 sentence-transformers / faiss，用 BM25 + 倒排索引，纯 Python。
适合小规模（几千条）本地文档检索。数据存 SQLite。

工具：
  - memory.add(text, tags?) —— 存一条记忆
  - memory.search(query, limit?) —— BM25 检索
  - memory.list(limit?) —— 列最近的
  - memory.delete(id) —— 删一条

调用方式（stdio JSON-RPC）：
  python -m mcp_memory_server <db_path>
"""
from __future__ import annotations
import json
import math
import sqlite3
import sys
import re
import time
from pathlib import Path


# ─── BM25 简易实现（无外部依赖）────────────────────────────────
def tokenize(text: str) -> list[str]:
    """粗糙分词：中文按字，英文按单词。"""
    text = text.lower()
    # 英文单词
    words = re.findall(r"[a-z0-9]+", text)
    # 中文单字
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    return words + chars


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        # unicode61 分词，去掉 diacritics + 保留 unicode 类别
        # 关键：tokenchars=" " 让中文每个字都成为独立 token（fallback：把中文文本预分词后存入）
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            text TEXT NOT NULL,
            tags TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            text_indexed, tags, content=''
        );
        """)
        self.conn.commit()

    def _tokenize_for_index(self, text: str) -> str:
        """把文本预分词成空格分隔——中文按字，英文按词。"""
        return " ".join(tokenize(text))

    def add(self, text: str, tags: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO memory(ts, text, tags) VALUES(?, ?, ?)",
            (time.time(), text, tags),
        )
        new_id = cur.lastrowid
        # 手动写 FTS：rowid = memory.id，text 用分词版
        self.conn.execute(
            "INSERT INTO memory_fts(rowid, text_indexed, tags) VALUES(?, ?, ?)",
            (new_id, self._tokenize_for_index(text), tags),
        )
        self.conn.commit()
        return new_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        # 用同样的 tokenize 处理 query，然后 OR 起来
        tokens = tokenize(query)
        if not tokens:
            return []
        # FTS5 syntax：单字用 quote 避免特殊字符
        fts_query = " OR ".join(f'"{t}"' for t in tokens[:20])
        try:
            rows = self.conn.execute(
                "SELECT m.id, m.ts, m.text, m.tags, bm25(memory_fts) as rank "
                "FROM memory_fts JOIN memory m ON memory_fts.rowid = m.id "
                "WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    def list(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, text, tags FROM memory ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memory WHERE id = ?", (id,))
        self.conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (id,))
        self.conn.commit()
        return cur.rowcount > 0


# ─── JSON-RPC over stdio（与 mcp_builtin_servers 里的其他 server 保持格式）───
TOOLS = [
    {
        "name": "add",
        "description": "存一条记忆到本地 SQLite。返回新条目 id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "记忆内容"},
                "tags": {"type": "string", "description": "标签，逗号分隔（可选）"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "search",
        "description": "在本地记忆里全文检索，返回相关条目按相关性排序。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回条数（默认 5）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list",
        "description": "列最近的记忆条目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数（默认 20）"},
            },
        },
    },
    {
        "name": "delete",
        "description": "按 id 删除一条记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
]


def handle(store: Store, req: dict) -> dict:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params", {})

    def result(data):
        return {"jsonrpc": "2.0", "id": rid, "result": data}

    def error(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return result({
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "memory", "version": "1.0"},
            "capabilities": {"tools": {}},
        })
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "add":
                new_id = store.add(args["text"], args.get("tags", ""))
                out = {"id": new_id, "ok": True}
            elif name == "search":
                out = {"results": store.search(args["query"], args.get("limit", 5))}
            elif name == "list":
                out = {"results": store.list(args.get("limit", 20))}
            elif name == "delete":
                out = {"ok": store.delete(args["id"])}
            else:
                return error(-32601, f"未知工具 {name}")
            return result({
                "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]
            })
        except Exception as e:
            return error(-32000, str(e))
    return error(-32601, f"未知方法 {method}")


def main() -> None:
    # 参数：memory server 用 <data_dir>/memory.db
    db_path = sys.argv[1] if len(sys.argv) > 1 else "memory.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(store, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
