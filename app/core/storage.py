"""storage.py — 原子读写与路径处理。

写入流程一律：读取 → 校验（由调用方执行）→ 写临时文件 → os.replace 原子替换。
临时文件与目标同目录，保证 replace 在同一文件系统上是原子操作。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    """当前本地时间的 ISO 8601 表示，带本地时区偏移。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path):
    """读取 UTF-8 JSON；文件不存在返回 None；损坏时抛出异常（由调用方转为校验错误）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """原子写入二进制内容。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data) -> None:
    """原子写入 JSON（UTF-8、缩进 2、保留中文原文）。"""
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, payload)


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入 UTF-8 文本。"""
    atomic_write_bytes(path, text.encode("utf-8"))
