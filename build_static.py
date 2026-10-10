"""构建静态 JSON。

读取 ``data/{module}/sentences.jsonl``，生成可部署到静态托管平台的 JSON：

    public/api/practical/sentences.json    全量句子数组
    public/api/practical/tags.json         标签及出现次数
    public/api/continuous/sentences.json
    public/api/continuous/tags.json

用法：
    python build_static.py                  # 生成静态 JSON
    python build_static.py --write-config   # 同时把 config.js 切到静态只读模式
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DIR = BASE_DIR / "public"
API_DIR = PUBLIC_DIR / "api"
MODULES = ("practical", "continuous")

LOCAL_CONFIG = """// 本地开发时使用（由 build_static.py --write-config 生成）
window.APP_CONFIG = {
  mode: "local",
  apiBase: "http://127.0.0.1:8000/api"
};
"""

STATIC_CONFIG = """// 部署到静态托管时使用（由 build_static.py --write-config 生成）
window.APP_CONFIG = {
  mode: "static",
  apiBase: "/api"
};
"""


def read_sentences(module: str) -> List[dict]:
    """读取并规范化一个分区的全部句子。"""
    path = DATA_DIR / module / "sentences.jsonl"
    if not path.exists():
        print(f"  [warn] {path} 不存在，按 0 条处理")
        return []

    items: List[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [warn] {path}:{lineno} 不是合法 JSON，已跳过")
                continue

            translation = obj.get("translation")
            items.append({
                "id": int(obj.get("id", 0)),
                "content": str(obj.get("content") or ""),
                "translation": None if translation is None else str(translation),
                "tags": [str(t).strip() for t in (obj.get("tags") or []) if str(t).strip()],
            })

    items.sort(key=lambda it: it["id"])
    return items


def build_tags(items: List[dict]) -> List[dict]:
    """标签统计：去重后按出现次数降序，次数相同按名称排序。"""
    counter: dict = {}
    for item in items:
        for tag in set(item["tags"]):
            counter[tag] = counter.get(tag, 0) + 1
    return [{"tag": name, "count": count}
            for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def write_config(static: bool) -> None:
    target = PUBLIC_DIR / "config.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STATIC_CONFIG if static else LOCAL_CONFIG, encoding="utf-8", newline="\n")


def write_data_js(module: str, items: List[dict], tags: List[dict]) -> None:
    """额外生成 data.js：以 <script> 方式暴露同一份数据。

    双击以 file:// 打开页面时浏览器禁止 fetch 本地 JSON，
    前端会改用 script 标签加载这份文件，保证离线双击也能浏览。
    """
    target = API_DIR / module / "data.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"sentences": items, "tags": tags}, ensure_ascii=False)
    text = ("window.__CORPUS_DATA__ = window.__CORPUS_DATA__ || {};\n"
            f'window.__CORPUS_DATA__["{module}"] = {payload};\n')
    target.write_text(text, encoding="utf-8", newline="\n")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 public/api 下的静态 JSON")
    parser.add_argument("--write-config", action="store_true",
                        help="把 public/config.js 切换为静态只读模式（mode=static）")
    parser.add_argument("--local-config", action="store_true",
                        help="把 public/config.js 切换回本地编辑模式（mode=local）")
    args = parser.parse_args(argv)

    if args.write_config and args.local_config:
        parser.error("--write-config 与 --local-config 不能同时使用")

    print(f"数据源：{DATA_DIR}")
    print(f"输出：{API_DIR}")

    for module in MODULES:
        items = read_sentences(module)
        tags = build_tags(items)
        write_json(API_DIR / module / "sentences.json", items)
        write_json(API_DIR / module / "tags.json", tags)
        write_data_js(module, items, tags)
        print(f"  [{module}] 句子 {len(items)} 条，标签 {len(tags)} 个 -> "
              f"{API_DIR / module / 'sentences.json'}")

    if args.write_config:
        write_config(True)
        print(f"  config.js -> 静态只读模式（mode=static）")
    elif args.local_config:
        write_config(False)
        print(f"  config.js -> 本地编辑模式（mode=local）")

    print("构建完成。部署时请把 public/ 目录作为站点根目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
