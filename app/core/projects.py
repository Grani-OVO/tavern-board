"""projects.py — 项目的创建、读取、更新、归档、筛选与全局校验。

数据布局（数据根目录下）：
    manifest.json        schema_version + last_backup
    projects/<pid>/      project.json + PROJECT.md
    tickets/             工单 Markdown
    templates/           project-template.md（新建项目模板）
    backups/             本地 ZIP 快照

项目列表以扫描 projects/ 目录为准，manifest 不存项目清单，避免双写漂移。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from . import schema
from .storage import atomic_write_json, atomic_write_text, now_iso, read_json

MANIFEST_NAME = "manifest.json"
PROJECTS_DIR = "projects"
TICKETS_DIR = "tickets"
TEMPLATES_DIR = "templates"
BACKUPS_DIR = "backups"
TEMPLATE_NAME = "project-template.md"

PROJECT_TEMPLATE = """# {name}

> 结构化状态（阶段、进度、下一步等）以同目录 `project.json` 为准；
> 本文件保存背景、技术决策与约定，供人与 Agent 长期维护。

## 目标

{goal}

## 技术决策与约定

| 日期 | 决策 | 理由 |
|---|---|---|
| {date} | 项目建档 | 初始化 |
"""


def new_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    return f"p-{today}-{secrets.token_hex(3)}"


def manifest_path(data_root: Path) -> Path:
    return Path(data_root) / MANIFEST_NAME


def projects_dir(data_root: Path) -> Path:
    return Path(data_root) / PROJECTS_DIR


def project_dir(data_root: Path, pid: str) -> Path:
    return projects_dir(data_root) / pid


def init_data_root(data_root: Path) -> Path:
    """初始化数据根目录的完整布局；已存在的目录保持不动（幂等）。"""
    data_root = Path(data_root)
    for sub in (PROJECTS_DIR, TICKETS_DIR, TEMPLATES_DIR, BACKUPS_DIR):
        (data_root / sub).mkdir(parents=True, exist_ok=True)
    template = data_root / TEMPLATES_DIR / TEMPLATE_NAME
    if not template.exists():
        atomic_write_text(template, PROJECT_TEMPLATE)
    manifest = read_json(manifest_path(data_root))
    if manifest is None:
        atomic_write_json(manifest_path(data_root), {
            "schema_version": schema.SCHEMA_VERSION,
            "last_backup": {"time": None, "path": None},
            "updated_at": now_iso(),
        })
    return data_root


def create_project(data_root: Path, *, name: str, goal: str = "",
                   status: str = "需求确认", tech_stack: list[str] | None = None,
                   tags: list[str] | None = None, next_step: str = "",
                   tickets: list[str] | None = None, project_id: str | None = None,
                   extra: dict | None = None) -> dict:
    """新建项目：生成稳定 ID、project.json、PROJECT.md 与关联目录。"""
    data_root = Path(data_root)
    init_data_root(data_root)
    pid = project_id or new_id()
    errors = schema.validate_project({
        "schema_version": schema.SCHEMA_VERSION, "id": pid, "name": name,
        "status": status, "created_at": now_iso(), "updated_at": now_iso(),
    })
    if errors:
        raise ValueError("新建项目参数非法: " + "; ".join(errors))
    if project_dir(data_root, pid).exists():
        raise ValueError(f"项目目录已存在: {pid}")

    timestamp = now_iso()
    data = {
        "schema_version": schema.SCHEMA_VERSION,
        "id": pid,
        "name": name,
        "status": status,
        "goal": goal,
        "tech_stack": tech_stack or [],
        "next_step": next_step,
        "blocked": None,
        "progress": 0,
        "tags": tags or [],
        "todos": [],
        "tickets": tickets or [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "archived_at": None,
    }
    if extra:  # Agent 或未来版本补充的字段，创建时即可带上
        data.update(extra)

    pdir = project_dir(data_root, pid)
    pdir.mkdir(parents=True)
    atomic_write_json(pdir / "project.json", data)
    template = (data_root / TEMPLATES_DIR / TEMPLATE_NAME).read_text(encoding="utf-8")
    atomic_write_text(pdir / "PROJECT.md", template.format(
        name=name, goal=goal or "（待补充）", date=timestamp[:10]))
    return data


def _load_one(pdir: Path) -> tuple[dict | None, list[str]]:
    """加载单个项目目录；返回 (data, errors)。损坏时 data 为 None。"""
    pid = pdir.name
    errors: list[str] = []
    json_path = pdir / "project.json"
    try:
        data = read_json(json_path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return None, [f"[{pid}] project.json 无法读取/解析: {exc}"]
    if data is None:
        return None, [f"[{pid}] 缺少 project.json"]
    errors.extend(f"[{pid}] {e}" for e in schema.validate_project(data))
    if isinstance(data, dict) and data.get("id") != pid:
        errors.append(f"[{pid}] id ({data.get('id')!r}) 与目录名不一致")
    if not (pdir / "PROJECT.md").exists():
        errors.append(f"[{pid}] 缺少 PROJECT.md")
    return data, errors


def list_projects(data_root: Path, *, include_archived: bool = False,
                  status: str | None = None, tag: str | None = None,
                  q: str | None = None) -> list[dict]:
    """扫描 projects/ 目录返回项目列表（可按阶段、标签、名称检索）。"""
    data_root = Path(data_root)
    results = []
    pdir_root = projects_dir(data_root)
    if not pdir_root.exists():
        return []
    for pdir in sorted(pdir_root.iterdir()):
        if not pdir.is_dir():
            continue
        data, errors = _load_one(pdir)
        if data is None:
            continue  # 损坏项目不在列表展示，由 validate 命令报告
        if errors:
            continue
        if not include_archived and data.get("status") == "归档":
            continue
        if status and data.get("status") != status:
            continue
        if tag and tag not in (data.get("tags") or []):
            continue
        if q:
            needle = q.lower()
            haystack = " ".join([
                str(data.get("name", "")), str(data.get("goal", "")),
                str(data.get("next_step") or ""),
            ]).lower()
            if needle not in haystack:
                continue
        results.append(data)
    results.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return results


def get_project(data_root: Path, pid: str) -> dict:
    """读取单个项目；不存在或损坏抛 ValueError（附带原因）。"""
    data, errors = _load_one(project_dir(Path(data_root), pid))
    if data is None:
        raise ValueError(f"项目不存在或不可读: {pid}（{'; '.join(errors)}）")
    if errors:
        raise ValueError(f"项目数据非法: {pid}（{'; '.join(errors)}）")
    return data


def get_project_markdown(data_root: Path, pid: str) -> str:
    path = project_dir(Path(data_root), pid) / "PROJECT.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def update_project(data_root: Path, pid: str, patch: dict) -> dict:
    """更新项目：读取 → 合并 → 校验 → 原子替换。

    - patch 只允许 UPDATEABLE_FIELDS 中的键，未知键报错（不静默忽略或删除）；
    - 原 JSON 中的未知字段（Agent 自行扩展的）原样保留写回；
    - 校验不通过时不写入，磁盘上原数据不受影响。
    """
    unknown = [k for k in patch if k not in schema.UPDATEABLE_FIELDS]
    if unknown:
        raise ValueError(f"更新请求包含未知/不可修改字段: {', '.join(sorted(unknown))}")
    data = get_project(data_root, pid)
    merged = dict(data)
    merged.update(patch)
    merged["updated_at"] = now_iso()
    errors = schema.validate_project(merged)
    if errors:
        raise ValueError("合并后数据非法，已拒绝写入: " + "; ".join(errors))
    atomic_write_json(project_dir(Path(data_root), pid) / "project.json", merged)
    return merged


def archive_project(data_root: Path, pid: str) -> dict:
    return update_project(data_root, pid, {"status": "归档", "archived_at": now_iso()})


def restore_project(data_root: Path, pid: str, status: str = "开发中") -> dict:
    if status not in schema.STATUSES or status == "归档":
        raise ValueError(f"恢复目标状态必须是归档以外的枚举值: {status!r}")
    return update_project(data_root, pid, {"status": status, "archived_at": None})


def list_archived(data_root: Path, q: str | None = None) -> list[dict]:
    return list_projects(data_root, include_archived=True, status="归档", q=q)


def list_backups(data_root: Path, limit: int = 10) -> list[dict]:
    """列出历史备份（新的在前），供备份状态页展示。"""
    backups_dir = Path(data_root) / BACKUPS_DIR
    if not backups_dir.exists():
        return []
    items = []
    for path in sorted(backups_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        items.append({
            "name": path.name,
            "rel_path": path.relative_to(data_root).as_posix(),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        })
    return items


def validate_all(data_root: Path) -> dict:
    """全库校验：所有 project.json、manifest、目录一致性。返回汇总报告。"""
    data_root = Path(data_root)
    report = {"ok": True, "projects": {}, "manifest": [], "other": []}
    pdir_root = projects_dir(data_root)
    if not pdir_root.exists():
        report["ok"] = False
        report["other"].append(f"缺少 {PROJECTS_DIR}/ 目录（数据根未初始化？）")
        return report

    for pdir in sorted(pdir_root.iterdir()):
        if not pdir.is_dir():
            report["other"].append(f"{PROJECTS_DIR}/ 下存在非目录文件: {pdir.name}")
            continue
        data, errors = _load_one(pdir)
        report["projects"][pdir.name] = errors
        if errors:
            report["ok"] = False

    try:
        manifest = read_json(manifest_path(data_root))
    except (json.JSONDecodeError, OSError) as exc:
        manifest = None
        report["manifest"].append(f"manifest.json 无法读取/解析: {exc}")
    if manifest is None and not report["manifest"]:
        report["manifest"].append("缺少 manifest.json")
    elif manifest is not None:
        report["manifest"].extend(schema.validate_manifest(manifest))
        last_backup = manifest.get("last_backup") or {}
        backup_rel = last_backup.get("path")
        if backup_rel and not (data_root / backup_rel).exists():
            report["manifest"].append(
                f"last_backup 指向的备份文件不存在: {backup_rel}（历史备份被移动或删除）")
    if report["manifest"]:
        report["ok"] = False
    if report["other"]:
        report["ok"] = False
    return report


def read_manifest(data_root: Path) -> dict:
    manifest = read_json(manifest_path(Path(data_root)))
    if manifest is None:
        return {"schema_version": schema.SCHEMA_VERSION,
                "last_backup": {"time": None, "path": None}}
    return manifest


def write_manifest(data_root: Path, manifest: dict) -> None:
    manifest = dict(manifest)
    manifest["schema_version"] = schema.SCHEMA_VERSION
    manifest["updated_at"] = now_iso()
    atomic_write_json(manifest_path(Path(data_root)), manifest)


def parse_backup_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def backup_is_due(data_root: Path, hours: int = 24) -> bool:
    """距上次成功备份是否超过 hours 小时（无备份记录视为到期）。"""
    last = read_manifest(data_root).get("last_backup") or {}
    last_time = parse_backup_time(last.get("time"))
    if last_time is None:
        return True
    return datetime.now().astimezone() - last_time > timedelta(hours=hours)
