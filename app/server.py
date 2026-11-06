"""server.py — 本地 Web 服务。

- 仅监听 127.0.0.1，不对外；
- 静态页面 + JSON API；
- 所有写操作走「读取 → 校验 → 原子替换」，非法数据不会落盘；
- 每次写操作后做自动备份节流检查（距上次 >24h 才快照）。
"""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .core import backup, config, projects
from .core.schema import STATUSES
from .core.storage import now_iso

STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _require_data_root() -> Path:
    root = config.get_data_root()
    if root is None:
        raise ApiError(409, "尚未完成首次启动配置，请先在设置页选择数据目录")
    return root


def _auto_backup(data_root: Path) -> None:
    try:
        backup.auto_backup_if_due(data_root)
    except Exception:  # 自动备份失败不阻塞主操作，状态页可见
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "PMS/1.0"

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):  # 精简日志
        print(f"[pms] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data=None) -> None:
        self._send_json(200, {"ok": True, "data": data})

    def _fail(self, status: int, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "请求体不是合法的 UTF-8 JSON")

    def _serve_static(self, rel: str) -> None:
        target = (STATIC_ROOT / rel).resolve()
        if not str(target).startswith(str(STATIC_ROOT.resolve())) or not target.is_file():
            self._fail(404, f"未找到资源: {rel}")
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif target.suffix in (".js", ".mjs"):
            mime = "text/javascript; charset=utf-8"
        elif target.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif target.suffix in (".ttf",):
            mime = "font/ttf"
        elif target.suffix in (".woff",):
            mime = "font/woff"
        elif target.suffix in (".woff2",):
            mime = "font/woff2"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            self._route_get()
        except ApiError as exc:
            self._fail(exc.status, exc.message)
        except ValueError as exc:
            self._fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._fail(500, f"服务器内部错误: {exc}")

    def do_POST(self):
        try:
            self._route_post()
        except ApiError as exc:
            self._fail(exc.status, exc.message)
        except Exception as exc:  # noqa: BLE001
            self._fail(500, f"服务器内部错误: {exc}")

    def do_PATCH(self):
        self.do_POST()

    def _route_get(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        # 静态页面
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path in ("/detail.html", "/admin.html"):
            return self._serve_static(path.lstrip("/"))
        if path.startswith("/css/") or path.startswith("/js/") or path.startswith("/fonts/"):
            return self._serve_static(path.lstrip("/"))

        # API
        if path == "/api/health":
            # 轻量存活检查：不依赖数据目录配置，供自动化探活
            return self._ok({"alive": True, "now": now_iso()})

        if path == "/api/bootstrap":
            root = config.get_data_root()
            return self._ok({
                "configured": root is not None,
                "data_root": str(root) if root else None,
                "statuses": STATUSES,
                "now": now_iso(),
            })

        if path == "/api/projects":
            data_root = _require_data_root()
            include_archived = query.get("include_archived") == "1"
            if query.get("archived_only") == "1":
                items = projects.list_archived(data_root, q=query.get("q"))
            else:
                items = projects.list_projects(
                    data_root,
                    include_archived=include_archived,
                    status=query.get("status") or None,
                    tag=query.get("tag") or None,
                    q=query.get("q") or None,
                )
            return self._ok(items)

        if path.startswith("/api/projects/"):
            pid = path.split("/")[3]
            data_root = _require_data_root()
            if path.endswith("/markdown"):
                return self._ok({"markdown": projects.get_project_markdown(data_root, pid)})
            return self._ok(projects.get_project(data_root, pid))

        if path == "/api/status":
            data_root = _require_data_root()
            manifest = projects.read_manifest(data_root)
            active = projects.list_projects(data_root)
            archived = projects.list_projects(data_root, include_archived=True, status="归档")
            report = projects.validate_all(data_root)
            last = manifest.get("last_backup") or {}
            project_errors = []
            for pid, errors in report["projects"].items():
                project_errors.extend(errors)
            return self._ok({
                "data_root": str(data_root),
                "project_count": len(active),
                "archived_count": len(archived),
                "last_backup": last,
                "backups": projects.list_backups(data_root),
                "validation_ok": report["ok"],
                "validation_errors": report["manifest"] + report["other"] + project_errors,
                "now": now_iso(),
            })

        if path == "/api/file":
            data_root = _require_data_root()
            rel = (query.get("path") or "").replace("\\", "/")
            from .core.schema import validate_relative_path
            problem = validate_relative_path(rel or "")
            if problem:
                raise ApiError(400, f"非法路径: {problem}")
            if not (rel.startswith("tickets/") or rel.startswith("projects/")):
                raise ApiError(400, "只允许读取 tickets/ 与 projects/ 下的文件")
            target = (data_root / rel).resolve()
            if not str(target).startswith(str(Path(data_root).resolve())):
                raise ApiError(400, "路径越界")
            if not target.is_file():
                raise ApiError(404, f"文件不存在: {rel}")
            if target.suffix != ".md":
                raise ApiError(400, "只支持读取 .md 文件")
            return self._ok({"path": rel, "content": target.read_text(encoding="utf-8")})

        raise ApiError(404, f"未找到接口: {path}")

    def _route_post(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        body = self._read_body()

        if path == "/api/setup":
            if body.get("zip_path"):
                result = backup.restore_backup(Path(body["zip_path"]))
                if result["errors"]:
                    raise ApiError(400, "恢复失败: " + "; ".join(result["errors"]))
                dest = result["dest"]
                config.set_data_root(dest)
                return self._ok({"data_root": str(dest), "restored": True})
            data_root = body.get("data_root")
            if not data_root:
                raise ApiError(400, "需要 data_root 或 zip_path")
            projects.init_data_root(Path(data_root))
            config.set_data_root(Path(data_root))
            return self._ok({"data_root": str(Path(data_root).resolve()), "restored": False})

        if path == "/api/activate-root":
            data_root = body.get("data_root")
            if not data_root:
                raise ApiError(400, "需要 data_root")
            target = Path(data_root)
            if not (target / projects.MANIFEST_NAME).is_file():
                raise ApiError(400, f"目标目录不是合法数据根（缺少 manifest.json）: {data_root}")
            config.set_data_root(target)
            return self._ok({"data_root": str(target.resolve())})

        if path == "/api/backup":
            data_root = _require_data_root()
            zip_path = backup.create_backup(data_root, reason="manual")
            return self._ok({"zip_path": str(zip_path),
                             "rel_path": zip_path.relative_to(data_root).as_posix()})

        if path == "/api/restore":
            data_root = _require_data_root()
            zip_path = body.get("zip_path")
            if not zip_path:
                raise ApiError(400, "需要 zip_path")
            # 恢复前先对当前数据强制快照（工单要求：先备份再恢复）
            safety = backup.create_backup(data_root, reason="pre-restore-safety")
            result = backup.restore_backup(Path(zip_path))
            return self._ok({
                "dest": str(result["dest"]) if result["dest"] else None,
                "errors": result["errors"],
                "safety_backup": str(safety),
            })

        if path == "/api/projects":
            data_root = _require_data_root()
            for field in ("name",):
                if not str(body.get(field) or "").strip():
                    raise ApiError(400, f"缺少必填字段: {field}")
            try:
                data = projects.create_project(
                    data_root,
                    name=str(body["name"]).strip(),
                    goal=str(body.get("goal") or ""),
                    status=body.get("status") or "需求确认",
                    tech_stack=body.get("tech_stack") or [],
                    tags=body.get("tags") or [],
                    next_step=str(body.get("next_step") or ""),
                )
            except ValueError as exc:
                raise ApiError(400, str(exc))
            _auto_backup(data_root)
            return self._ok(data)

        if path.startswith("/api/projects/"):
            data_root = _require_data_root()
            parts = path.split("/")
            pid = parts[3]
            action = parts[4] if len(parts) > 4 else None

            if action == "archive":
                return self._ok(projects.archive_project(data_root, pid))
            if action == "markdown":  # 保存 PROJECT.md（原子写）
                content = body.get("content")
                if not isinstance(content, str):
                    raise ApiError(400, "需要 content 字符串")
                md_path = projects.project_dir(data_root, pid) / "PROJECT.md"
                if not md_path.parent.exists():
                    raise ApiError(404, f"项目目录不存在: {pid}")
                from .core.storage import atomic_write_text
                atomic_write_text(md_path, content)
                return self._ok({"saved": True})
            if action == "restore":
                try:
                    return self._ok(projects.restore_project(
                        data_root, pid, body.get("status") or "开发中"))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
            if action is None:  # PATCH 更新
                if not isinstance(body, dict) or not body:
                    raise ApiError(400, "PATCH 请求体必须是非空对象")
                try:
                    return self._ok(projects.update_project(data_root, pid, body))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
            raise ApiError(404, f"未知操作: {action}")

        raise ApiError(404, f"未找到接口: {path}")


def run(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"个人项目管理系统已启动: {url}  （关闭本窗口或 Ctrl+C 停止）")
    import threading
    import webbrowser
    threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    run()
