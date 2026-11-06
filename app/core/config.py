"""config.py — 运行配置管理。

app-config.json 只保存一项关键信息：数据根目录的绝对路径。
它位于代码目录（不入库、不进备份包），备份包内部只使用相对路径。
"""
from __future__ import annotations

from pathlib import Path

from .storage import atomic_write_json, now_iso, read_json

APP_ROOT = Path(__file__).resolve().parent.parent
APP_CONFIG_PATH = APP_ROOT / "app-config.json"


def load_config() -> dict:
    return read_json(APP_CONFIG_PATH) or {}


def get_data_root() -> Path | None:
    config = load_config()
    raw = config.get("data_root")
    if not raw:
        return None
    return Path(raw)


def set_data_root(path: Path) -> None:
    atomic_write_json(APP_CONFIG_PATH, {
        "data_root": str(Path(path).resolve()),
        "updated_at": now_iso(),
    })


def is_configured() -> bool:
    return get_data_root() is not None
