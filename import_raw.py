"""原始语料导入脚本。

把 ``raw/{module}/`` 下的原始文本转换成对应分区的 ``data/{module}/sentences.jsonl``。

支持输入格式：
    .txt  一行一句（忽略空行）
    .md   按空行切分段落，非空段落作为一条
    .csv  列名映射 ``content`` / ``translation`` / ``tags``；tags 用 ``|`` 分隔

处理顺序：先 .txt，再 .md，最后 .csv。
去重规则：与已有 JSONL 的 ``content`` 完全相同则跳过。

用法：
    python import_raw.py                     # 导入两个分区
    python import_raw.py practical           # 只导入应用文分区
    python import_raw.py continuous --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = BASE_DIR / "raw"
MODULES = ("practical", "continuous")
SUPPORTED_SUFFIXES = (".txt", ".md", ".csv")


def jsonl_path(module: str) -> Path:
    return DATA_DIR / module / "sentences.jsonl"


def next_id_path(module: str) -> Path:
    return DATA_DIR / module / "next_id.txt"


def clean_tags(tags) -> List[str]:
    """去空、去重，保持顺序。"""
    seen, result = set(), []
    for tag in tags:
        name = str(tag).strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def split_tags(text: str) -> List[str]:
    """tags 用 ``|`` 分隔，同时兼容中英文逗号。"""
    normalized = text.replace("|", ",").replace("，", ",").replace("、", ",")
    return clean_tags(normalized.split(","))


def load_existing(module: str) -> Tuple[List[dict], set]:
    """读取现有 JSONL，返回记录列表与 content 去重集合。"""
    path = jsonl_path(module)
    items: List[dict] = []
    if not path.exists():
        return items, set()

    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.lstrip("\ufeff").strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  [warn] {path}:{lineno} 不是合法 JSON，已跳过")
    return items, {str(it.get("content", "")).strip() for it in items}


def determine_next_id(module: str, items: List[dict]) -> int:
    """优先使用 next_id.txt，否则用 max(id)+1。"""
    path = next_id_path(module)
    if path.exists():
        try:
            value = int(path.read_text(encoding="utf-8").strip())
            if value >= 1:
                return value
        except (ValueError, OSError):
            print(f"  [warn] {path} 内容非法，改用 max(id)+1 推导")
    return max((int(it.get("id", 0)) for it in items), default=0) + 1


# --------------------------------------------------------------------------- #
# 各格式解析
# --------------------------------------------------------------------------- #


def parse_txt(path: Path) -> List[dict]:
    """一行一句。"""
    records: List[dict] = []
    text = path.read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        content = line.strip()
        if content:
            records.append({"content": content, "translation": None, "tags": []})
    return records


def parse_md(path: Path) -> List[dict]:
    """按空行切分段落，非空段落作为一条。"""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    records: List[dict] = []
    for block in text.split("\n\n"):
        # 段落内部若有换行（软换行），合并为一行
        content = " ".join(part.strip() for part in block.splitlines() if part.strip())
        content = content.strip()
        if content:
            records.append({"content": content, "translation": None, "tags": []})
    return records


def parse_csv(path: Path) -> List[dict]:
    """列名映射 content / translation / tags。

    若某行列数多于表头（常见原因：CSV 字段里的逗号没有加引号），
    则多出的片段会并回 ``translation`` 并给出警告，避免脏数据被当成标签。
    """
    records: List[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            raw_header = next(reader)
        except StopIteration:
            return records
        if not raw_header:
            return records

        # 容忍列名大小写与首尾空格差异
        header = [name.strip().lower() for name in raw_header]
        if "content" not in header:
            print(f"  [warn] {path.name} 缺少 content 列，已跳过该文件")
            return records

        has_content = header.index("content")
        has_translation = header.index("translation") if "translation" in header else None
        has_tags = header.index("tags") if "tags" in header else None
        expected = len(header)

        for lineno, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue

            if len(row) > expected and has_translation is not None:
                # 例：译文里带未转义的逗号，被切成了多列——合并回译文
                extra = len(row) - expected
                merged = ",".join(cell for cell in row[has_translation:has_translation + extra + 1])
                row = row[:has_translation] + [merged] + row[has_translation + extra + 1:]
                print(f"  [warn] {path.name}:{lineno} 列数多于表头，已按未转义逗号合并译文")

            def cell(index):
                if index is None or index >= len(row):
                    return ""
                return (row[index] or "").strip()

            content = cell(has_content)
            if not content:
                continue
            translation = cell(has_translation)
            tags = split_tags(cell(has_tags)) if has_tags is not None else []
            records.append({
                "content": content,
                "translation": translation or None,
                "tags": tags,
            })
    return records


PARSERS = {".txt": parse_txt, ".md": parse_md, ".csv": parse_csv}


def iter_source_files(module: str) -> List[Path]:
    """按 .txt → .md → .csv 的顺序返回该分区下的原始文件。"""
    folder = RAW_DIR / module
    if not folder.is_dir():
        return []
    found: List[Path] = []
    for suffix in SUPPORTED_SUFFIXES:
        found.extend(sorted(p for p in folder.glob(f"*{suffix}") if p.is_file()))
    return found


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def import_module(module: str, dry_run: bool = False) -> Tuple[int, int]:
    """导入一个分区，返回 (导入条数, 跳过重复条数)。"""
    print(f"\n=== 分区：{module} ===")
    files = iter_source_files(module)
    if not files:
        print(f"  （raw/{module}/ 下没有 .txt/.md/.csv 文件，跳过）")
        return 0, 0

    items, contents = load_existing(module)
    next_id = determine_next_id(module, items)
    print(f"  现有 {len(items)} 条，下一个可用 id = {next_id}")

    imported, skipped = 0, 0
    new_records: List[dict] = []

    for path in files:
        parser = PARSERS[path.suffix.lower()]
        try:
            parsed = parser(path)
        except OSError as exc:
            print(f"  [error] 读取 {path.name} 失败：{exc}")
            continue

        file_imported, file_skipped = 0, 0
        for record in parsed:
            content = str(record["content"]).strip()
            if not content:
                continue
            if content in contents:          # 文件之间、文件与库之间都去重
                file_skipped += 1
                continue
            contents.add(content)
            new_records.append({
                "id": next_id,
                "content": content,
                "translation": record.get("translation") or None,
                "tags": clean_tags(record.get("tags") or []),
            })
            next_id += 1
            file_imported += 1

        imported += file_imported
        skipped += file_skipped
        print(f"  [{path.suffix.lstrip('.')}] {path.name}：导入 {file_imported} 条，跳过重复 {file_skipped} 条")

    if dry_run:
        print(f"  --dry-run：未写入。预计导入 {imported} 条，跳过重复 {skipped} 条")
        return imported, skipped

    if new_records:
        write_new_records(module, new_records)
        write_next_id(module, next_id)

    print(f"  完成：导入 {imported} 条，跳过重复 {skipped} 条，文件共 {len(items) + imported} 条")
    return imported, skipped


def write_new_records(module: str, records: List[dict]) -> None:
    """以追加模式写入新记录（不重写整个文件）。"""
    path = jsonl_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_next_id(module: str, value: int) -> None:
    path = next_id_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(str(int(value)), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 raw/ 下的原始语料导入 data/*/sentences.jsonl")
    parser.add_argument("modules", nargs="*", default=list(MODULES),
                        help=f"要导入的分区，默认全部：{'/'.join(MODULES)}")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args(argv)

    modules = args.modules or list(MODULES)
    unknown = [m for m in modules if m not in MODULES]
    if unknown:
        parser.error(f"未知分区：{', '.join(unknown)}（可选：{', '.join(MODULES)}）")

    print(f"原始语料目录：{RAW_DIR}")
    total_imported = total_skipped = 0
    for module in modules:
        imported, skipped = import_module(module, dry_run=args.dry_run)
        total_imported += imported
        total_skipped += skipped

    print(f"\n合计：导入 {total_imported} 条，跳过重复 {total_skipped} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
