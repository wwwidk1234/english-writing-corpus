"""英语写作语料库 · 本地 FastAPI 后端。

仅在本机运行，提供 practical / continuous 两个分区的增删改查、分页、搜索与标签筛选。

运行：
    python main.py
    # 或
    uvicorn main:app --reload --host 127.0.0.1 --port 8000

文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# 常量与分区定义
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

MODULES = ("practical", "continuous")
MAX_PAGE_SIZE = 100
MAX_BACKUPS = 20

# 每个分区一把锁，保证同一分区的读改写串行化
_LOCKS = {module: threading.Lock() for module in MODULES}


def module_dir(module: str) -> Path:
    """校验分区名并返回该分区数据目录。"""
    if module not in MODULES:
        raise HTTPException(status_code=404, detail=f"未知分区：{module}")
    return DATA_DIR / module


def jsonl_path(module: str) -> Path:
    return module_dir(module) / "sentences.jsonl"


def strip_bom(text: str) -> str:
    """去掉文件开头的 BOM（记事本等编辑器的 UTF-8 会带 BOM）。"""
    return text[1:] if text.startswith("\ufeff") else text


def next_id_path(module: str) -> Path:
    return module_dir(module) / "next_id.txt"


def backup_dir(module: str) -> Path:
    return module_dir(module) / "backup"


# --------------------------------------------------------------------------- #
# 数据层：读取
# --------------------------------------------------------------------------- #


def read_all(module: str) -> List[dict]:
    """读取分区全部句子；文件不存在时返回空列表。"""
    path = jsonl_path(module)
    if not path.exists():
        return []

    items: List[dict] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = strip_bom(raw).strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 单行损坏不应导致整个接口失败，跳过并提示到 stderr
                print(f"[warn] {path}:{lineno} 不是合法 JSON，已跳过")
                continue
            items.append(normalize(obj))
    return items


def normalize(obj: dict) -> dict:
    """把一条记录补全为规范结构。"""
    content = obj.get("content") or ""
    translation = obj.get("translation")
    tags = obj.get("tags") or []
    return {
        "id": int(obj.get("id", 0)),
        "content": str(content),
        "translation": None if translation is None else str(translation),
        "tags": [str(t) for t in tags if str(t).strip()],
    }


def read_next_id(module: str) -> int:
    """读取下一个可用 ID；文件缺失或损坏时回退到 max(id)+1。"""
    path = next_id_path(module)
    if path.exists():
        try:
            value = int(path.read_text(encoding="utf-8").strip())
            if value >= 1:
                return value
        except (ValueError, OSError):
            print(f"[warn] {path} 内容非法，改用 max(id)+1 推导")

    items = read_all(module)
    return (max((it["id"] for it in items), default=0)) + 1


def write_next_id(module: str, value: int) -> None:
    path = next_id_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(str(int(value)), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def collect_tags(items: Iterable[dict]) -> List[str]:
    """标签去重并按出现次数降序（次数相同按名称排序，保证结果稳定）。"""
    counter: dict = {}
    for it in items:
        for tag in set(it["tags"]):
            counter[tag] = counter.get(tag, 0) + 1
    return [t for t, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def paginate(items: List[dict], page: int, page_size: int) -> dict:
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size)) if total else 0
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# --------------------------------------------------------------------------- #
# 数据层：写入
# --------------------------------------------------------------------------- #


def make_backup(module: str) -> Optional[Path]:
    """修改/删除前备份当前 JSONL，最多保留最近 MAX_BACKUPS 份。"""
    src = jsonl_path(module)
    if not src.exists():
        return None

    target_dir = backup_dir(module)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"sentences_{stamp}.jsonl"
    index = 1
    while target.exists():  # 同一秒内多次修改也不覆盖
        target = target_dir / f"sentences_{stamp}_{index}.jsonl"
        index += 1

    shutil.copy2(src, target)

    backups = sorted(target_dir.glob("sentences_*.jsonl"))
    for stale in backups[:-MAX_BACKUPS]:
        try:
            stale.unlink()
        except OSError:
            pass
    return target


def write_all_atomic(module: str, items: List[dict]) -> None:
    """读全部 → 改内存 → 写临时文件 → os.replace 原子替换。"""
    path = jsonl_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def append_one(module: str, record: dict) -> None:
    """新增时以追加模式写一行，不重写整个文件。"""
    path = jsonl_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def insert_items(module: str, payloads: List[dict]) -> List[dict]:
    """在分区锁内分配连续 ID 并落盘。"""
    with _LOCKS[module]:
        next_id = read_next_id(module)
        created: List[dict] = []
        for payload in payloads:
            record = {
                "id": next_id,
                "content": payload["content"],
                "translation": payload["translation"],
                "tags": payload["tags"],
            }
            next_id += 1
            created.append(record)

        for record in created:
            append_one(module, record)
        if created:
            write_next_id(module, next_id)
        return created


def update_item(module: str, item_id: int, payload: dict) -> dict:
    with _LOCKS[module]:
        items = read_all(module)
        for index, item in enumerate(items):
            if item["id"] == item_id:
                make_backup(module)
                items[index] = {
                    "id": item_id,
                    "content": payload["content"],
                    "translation": payload["translation"],
                    "tags": payload["tags"],
                }
                write_all_atomic(module, items)
                return items[index]
    raise HTTPException(status_code=404, detail=f"未找到 id={item_id} 的句子")


def delete_item(module: str, item_id: int) -> None:
    with _LOCKS[module]:
        items = read_all(module)
        remaining = [it for it in items if it["id"] != item_id]
        if len(remaining) == len(items):
            raise HTTPException(status_code=404, detail=f"未找到 id={item_id} 的句子")
        make_backup(module)
        write_all_atomic(module, remaining)


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #


class SentenceIn(BaseModel):
    content: str = Field(..., min_length=1, description="英文原文")
    translation: Optional[str] = Field(default=None, description="中文译文，可为 null")
    tags: List[str] = Field(default_factory=list, description="标签数组")

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("content 不能为空白")
        return text

    @field_validator("translation", mode="before")
    @classmethod
    def _blank_translation_to_none(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("tags", mode="before")
    @classmethod
    def _accept_string_tags(cls, value):
        """允许 tags 传字符串（逗号分隔）或 null。"""
        if value is None:
            return []
        if isinstance(value, str):
            return split_tags(value)
        return value

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        return clean_tags(value)


class BatchIn(BaseModel):
    items: List[SentenceIn] = Field(default_factory=list)


def split_tags(text: str) -> List[str]:
    """按中英文逗号拆分标签字符串。"""
    for sep in ("，", "、"):
        text = text.replace(sep, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def clean_tags(tags: Iterable[str]) -> List[str]:
    """去空、去重，保持原有顺序。"""
    seen = set()
    result: List[str] = []
    for tag in tags:
        name = str(tag).strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


# --------------------------------------------------------------------------- #
# 应用与路由
# --------------------------------------------------------------------------- #

app = FastAPI(
    title="英语写作语料库 API",
    description="高中英语应用文 / 读后续写素材的本地管理接口。",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/{module}/sentences", summary="分页获取句子列表")
def list_sentences(
    module: str,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE, description="每页条数，最大 100"),
    q: str = Query("", description="关键词"),
    scope: str = Query("all", pattern="^(all|content|translation)$", description="搜索范围"),
    tag: str = Query("", description="精确匹配标签"),
    sort: str = Query("id_desc", pattern="^(id_desc|id_asc)$", description="排序方式"),
) -> dict:
    module_dir(module)  # 校验分区
    items = read_all(module)

    keyword = q.strip().lower()
    if keyword:
        def hit(item: dict) -> bool:
            content = item["content"].lower()
            translation = (item["translation"] or "").lower()
            if scope == "content":
                return keyword in content
            if scope == "translation":
                return keyword in translation
            return keyword in content or keyword in translation

        items = [it for it in items if hit(it)]

    wanted = tag.strip()
    if wanted:
        items = [it for it in items if wanted in it["tags"]]

    items.sort(key=lambda it: it["id"], reverse=(sort == "id_desc"))
    return paginate(items, page, page_size)


@app.get("/api/{module}/tags", summary="获取标签列表（按出现次数降序）")
def list_tags(module: str) -> dict:
    module_dir(module)
    return {"tags": collect_tags(read_all(module))}


@app.get("/api/{module}/sentences/{item_id}", summary="获取单条句子")
def get_sentence(module: str, item_id: int) -> dict:
    module_dir(module)
    for item in read_all(module):
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail=f"未找到 id={item_id} 的句子")


@app.post("/api/{module}/sentences", status_code=201, summary="新增句子")
def create_sentence(module: str, payload: SentenceIn) -> dict:
    module_dir(module)
    created = insert_items(module, [payload.model_dump()])
    return created[0]


@app.put("/api/{module}/sentences/{item_id}", summary="修改句子")
def replace_sentence(module: str, item_id: int, payload: SentenceIn) -> dict:
    module_dir(module)
    return update_item(module, item_id, payload.model_dump())


@app.delete("/api/{module}/sentences/{item_id}", summary="删除句子")
def remove_sentence(module: str, item_id: int) -> dict:
    module_dir(module)
    delete_item(module, item_id)
    return {"ok": True}


@app.post("/api/{module}/sentences/batch", summary="批量导入")
def batch_create(module: str, payload: BatchIn) -> dict:
    module_dir(module)
    if not payload.items:
        return {"inserted": 0, "ids": []}
    created = insert_items(module, [it.model_dump() for it in payload.items])
    return {"inserted": len(created), "ids": [it["id"] for it in created]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
