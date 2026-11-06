"""schema.py — 数据校验规则。

所有校验共用本模块；CLI validate、API 写入、恢复前置校验都走这里。
原则：只校验已知字段的类型与取值；未知字段一律保留、不报错、不删除。
"""
from __future__ import annotations

import re
from datetime import datetime

SCHEMA_VERSION = 1

# 阶段固定枚举（工单定稿，不得增删改名）
STATUSES = [
    "需求确认",
    "开发中",
    "待验收",
    "已验收",
    "已上线",
    "暂停",
    "归档",
]

# 稳定项目 ID：p-YYYYMMDD-6 位小写十六进制
ID_PATTERN = re.compile(r"^p-\d{8}-[0-9a-f]{6}$")

# 必填字段（缺失即非法）
REQUIRED_FIELDS = ["schema_version", "id", "name", "status", "created_at", "updated_at"]

# 允许通过更新接口修改的字段；id / schema_version / created_at 不可改
UPDATEABLE_FIELDS = [
    "name", "status", "goal", "tech_stack", "next_step", "blocked",
    "progress", "tags", "todos", "tickets", "archived_at",
]

LIST_OF_STR_FIELDS = ["tech_stack", "tags"]


def _parse_iso_datetime(value: str):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def validate_datetime_field(data: dict, field: str, errors: list, required: bool):
    value = data.get(field)
    if value is None:
        if required:
            errors.append(f"必填字段缺失: {field}")
        return
    if not isinstance(value, str):
        errors.append(f"{field} 必须是 ISO 8601 字符串")
        return
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        errors.append(f"{field} 不是合法的 ISO 8601 日期时间: {value!r}")
        return
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        errors.append(f"{field} 缺少时区偏移（如 +08:00）: {value!r}")


def validate_relative_path(value: str) -> str | None:
    """校验数据包内部相对路径；返回错误消息或 None。"""
    if not isinstance(value, str) or not value:
        return "必须是非空字符串"
    if re.match(r"^[A-Za-z]:", value) or value.startswith("/") or value.startswith("\\"):
        return "不得是绝对路径"
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        return "不得包含 .."
    return None


def validate_project(data: dict) -> list[str]:
    """校验一个 project.json 的完整内容，返回错误列表（空即合法）。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["project.json 顶层必须是 JSON 对象"]

    for field in REQUIRED_FIELDS:
        if field not in data or data[field] is None:
            errors.append(f"必填字段缺失: {field}")

    if errors and "schema_version" in errors:
        return errors  # 无版本号时不再做进一步结构校验

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version 必须是 {SCHEMA_VERSION}，当前为 {data.get('schema_version')!r}"
        )

    pid = data.get("id")
    if not isinstance(pid, str) or not ID_PATTERN.match(pid):
        errors.append(f"id 必须形如 p-YYYYMMDD-6位十六进制，当前为 {pid!r}")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 必须是非空字符串")

    status = data.get("status")
    if status not in STATUSES:
        errors.append(f"status 必须是枚举值之一: {' / '.join(STATUSES)}，当前为 {status!r}")

    validate_datetime_field(data, "created_at", errors, required=True)
    validate_datetime_field(data, "updated_at", errors, required=True)
    validate_datetime_field(data, "archived_at", errors, required=False)

    progress = data.get("progress", 0)
    if not isinstance(progress, int) or isinstance(progress, bool) or not 0 <= progress <= 100:
        errors.append(f"progress 必须是 0-100 的整数，当前为 {progress!r}")

    blocked = data.get("blocked")
    if blocked is not None and not isinstance(blocked, str):
        errors.append("blocked 必须是字符串或 null")

    for field in LIST_OF_STR_FIELDS:
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            errors.append(f"{field} 必须是字符串数组")

    goal = data.get("goal")
    if goal is not None and not isinstance(goal, str):
        errors.append("goal 必须是字符串")

    next_step = data.get("next_step")
    if next_step is not None and not isinstance(next_step, str):
        errors.append("next_step 必须是字符串")

    todos = data.get("todos")
    if todos is not None:
        if not isinstance(todos, list):
            errors.append("todos 必须是数组")
        else:
            for i, item in enumerate(todos):
                if not isinstance(item, dict):
                    errors.append(f"todos[{i}] 必须是对象")
                    continue
                if not isinstance(item.get("text"), str) or not item["text"].strip():
                    errors.append(f"todos[{i}].text 必须是非空字符串")
                if not isinstance(item.get("done", False), bool):
                    errors.append(f"todos[{i}].done 必须是布尔值")

    tickets = data.get("tickets")
    if tickets is not None:
        if not isinstance(tickets, list):
            errors.append("tickets 必须是数组")
        else:
            for i, item in enumerate(tickets):
                problem = validate_relative_path(item) if isinstance(item, str) else "必须是字符串"
                if problem:
                    errors.append(f"tickets[{i}] {problem}: {item!r}")

    if data.get("archived_at") is not None and status != "归档":
        errors.append("archived_at 非 null 时 status 必须是 归档")
    if status == "归档" and data.get("archived_at") is None:
        errors.append("status 为 归档 时必须填写 archived_at")

    return errors


def validate_manifest(data: dict) -> list[str]:
    """校验 manifest.json。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest.json 顶层必须是 JSON 对象"]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"manifest schema_version 必须是 {SCHEMA_VERSION}")
    last_backup = data.get("last_backup")
    if last_backup is not None:
        if not isinstance(last_backup, dict):
            errors.append("last_backup 必须是对象或 null")
        else:
            problem = validate_relative_path(last_backup.get("path") or "")
            if last_backup.get("path") is not None and problem:
                errors.append(f"last_backup.path {problem}")
    return errors
