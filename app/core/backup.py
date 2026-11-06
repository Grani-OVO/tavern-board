"""backup.py — 本地 ZIP 备份与恢复。

备份：
- 命名 pms-backup-YYYYMMDD-HHMMSS.zip，存于数据根目录 backups/ 下；
- 打包 manifest.json、projects/、tickets/、templates/，一律使用相对路径；
- 排除 backups/ 自身（历史备份不嵌套）、.env、__pycache__、*.tmp、.DS_Store、Thumbs.db。

恢复：
- 解压到旁路新目录 <dest_parent>/pms-restored-<时间戳>，绝不覆盖现有数据；
- 解压前检查 ZIP 内路径安全（拒绝绝对路径与 ..），解压后执行全库校验；
- 校验通过后由调用方提示用户切换数据根目录。
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from . import projects as proj
from .storage import atomic_write_json

EXCLUDE_NAMES = {".env", "__pycache__", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = {".tmp", ".pyc"}
EXCLUDE_DIRS = {proj.BACKUPS_DIR}


def _excluded(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    if parts[0] in EXCLUDE_DIRS:
        return True
    if any(name in EXCLUDE_NAMES for name in parts):
        return True
    if any(Path(part).suffix in EXCLUDE_SUFFIXES for part in parts):
        return True
    return False


def _backup_stem() -> str:
    return f"pms-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def create_backup(data_root: Path, reason: str = "manual") -> Path:
    """生成 ZIP 快照并更新 manifest 的 last_backup（相对路径）。"""
    data_root = Path(data_root)
    backups_dir = data_root / proj.BACKUPS_DIR
    backups_dir.mkdir(parents=True, exist_ok=True)

    stem = _backup_stem()
    zip_path = backups_dir / f"{stem}.zip"
    suffix = 2
    while zip_path.exists():  # 同秒冲突时递增后缀，历史备份不删除不覆盖
        zip_path = backups_dir / f"{stem}-{suffix}.zip"
        suffix += 1

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(data_root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(data_root).as_posix()
            if _excluded(rel):
                continue
            zf.write(path, arcname=rel)

    manifest = proj.read_manifest(data_root)
    manifest["last_backup"] = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "path": zip_path.relative_to(data_root).as_posix(),
        "reason": reason,
    }
    proj.write_manifest(data_root, manifest)
    return zip_path


def auto_backup_if_due(data_root: Path, hours: int = 24) -> Path | None:
    """距上次备份超过 hours 小时则自动快照；未到期返回 None。"""
    if not proj.backup_is_due(data_root, hours):
        return None
    return create_backup(data_root, reason="auto")


def _check_zip_paths(zf: zipfile.ZipFile) -> list[str]:
    """检查 ZIP 内部路径安全；返回问题列表。"""
    problems = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if re.match(r"^[A-Za-z]:", name) or name.startswith("/"):
            problems.append(f"包含绝对路径条目: {info.filename}")
        if ".." in name.split("/"):
            problems.append(f"包含 .. 的条目: {info.filename}")
        # 拒绝解压出可执行脚本之外的危险链接条目（zip slip 的变体）
        if info.external_attr and (info.external_attr >> 16) & 0o170000 == 0o120000:
            problems.append(f"包含符号链接条目: {info.filename}")
    return problems


def restore_backup(zip_path: Path, dest_parent: Path | None = None) -> dict:
    """从 ZIP 恢复到旁路新目录，返回 {dest, errors}。

    - 不修改、不覆盖任何现有数据目录；
    - 解压后做全库校验，错误写入返回值而不抛异常（调用方展示）；
    - 校验通过则重置 manifest 的 last_backup（包内不含历史备份，旧记录失效）。
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return {"dest": None, "errors": [f"备份包不存在: {zip_path}"]}
    dest_parent = Path(dest_parent) if dest_parent else zip_path.parent.parent

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            problems = _check_zip_paths(zf)
            if problems:
                return {"dest": None, "errors": problems}
            names = set(zf.namelist())
            if proj.MANIFEST_NAME not in names:
                return {"dest": None, "errors": [f"备份包缺少 {proj.MANIFEST_NAME}，不是合法数据包"]}
            if f"{proj.PROJECTS_DIR}/" not in "".join(n + "/" for n in names):
                return {"dest": None, "errors": [f"备份包缺少 {proj.PROJECTS_DIR}/ 目录"]}

            dest = dest_parent / f"pms-restored-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            suffix = 2
            while dest.exists():
                dest = dest_parent / f"pms-restored-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
                suffix += 1
            dest.mkdir(parents=True)
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        return {"dest": None, "errors": [f"备份包无法读取: {exc}"]}

    # 包内不含 backups/ 历史，旧 last_backup 记录必然失效，先重置再校验
    manifest = proj.read_manifest(dest)
    manifest["last_backup"] = {"time": None, "path": None}
    manifest["restored_from"] = zip_path.name
    atomic_write_json(proj.manifest_path(dest), manifest)

    report = proj.validate_all(dest)
    errors = list(report["manifest"]) + list(report["other"])
    for pid, project_errors in report["projects"].items():
        errors.extend(project_errors)

    return {"dest": dest, "errors": errors}
