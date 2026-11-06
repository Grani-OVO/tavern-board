"""test_api.py — HTTP 接口层回归测试（真实起本地服务，走网络请求）。"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import server
from app.core import projects


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_root = Path(cls.tmp.name)
        projects.init_data_root(cls.data_root)

        # 用临时数据根替代 app-config，避免测试污染真实运行配置
        cls._patcher = patch.object(server.config, "get_data_root",
                                    return_value=cls.data_root)
        cls._patcher.start()

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        # 静默请求日志
        server.Handler.log_message = lambda *a, **k: None

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls._patcher.stop()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def call_raw(self, method, path, body=None):
        """返回 (status, payload)，不抛异常。"""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)


class TestProjectApi(ApiTestBase):
    def test_full_project_lifecycle(self):
        created = self.call("POST", "/api/projects", {"name": "API 测试项目"})["data"]
        pid = created["id"]
        listed = self.call("GET", "/api/projects")["data"]
        self.assertIn(pid, [p["id"] for p in listed])

        updated = self.call("PATCH", f"/api/projects/{pid}", {"status": "开发中"})["data"]
        self.assertEqual(updated["status"], "开发中")

        self.call("POST", f"/api/projects/{pid}/archive")
        self.assertEqual(self.call("GET", "/api/projects")["data"], [])
        archived = self.call("GET", "/api/projects?archived_only=1")["data"]
        self.assertEqual(len(archived), 1)

        restored = self.call("POST", f"/api/projects/{pid}/restore", {"status": "开发中"})["data"]
        self.assertIsNone(restored["archived_at"])

    def test_unknown_patch_field_is_400(self):
        created = self.call("POST", "/api/projects", {"name": "非法更新测试"})["data"]
        status, payload = self.call_raw("PATCH", f"/api/projects/{created['id']}",
                                        {"unknown_field": 1})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("unknown_field", payload["error"])

    def test_illegal_status_is_400(self):
        created = self.call("POST", "/api/projects", {"name": "非法状态测试"})["data"]
        status, payload = self.call_raw("PATCH", f"/api/projects/{created['id']}",
                                        {"status": "进行中"})
        self.assertEqual(status, 400)
        self.assertIn("枚举", payload["error"])

    def test_missing_project_is_400_or_error(self):
        status, _ = self.call_raw("GET", "/api/projects/p-20260101-000000")
        self.assertEqual(status, 400)

    def test_project_name_required(self):
        status, _ = self.call_raw("POST", "/api/projects", {"goal": "无名"})
        self.assertEqual(status, 400)

    def test_markdown_save_and_read(self):
        created = self.call("POST", "/api/projects", {"name": "档案测试"})["data"]
        pid = created["id"]
        content = "# 标题\n\n- 决策 A\n- 决策 B"
        self.call("POST", f"/api/projects/{pid}/markdown", {"content": content})
        md = self.call("GET", f"/api/projects/{pid}/markdown")["data"]["markdown"]
        self.assertEqual(md, content)
        # 非字符串 content 拒绝
        status, _ = self.call_raw("POST", f"/api/projects/{pid}/markdown", {"content": 5})
        self.assertEqual(status, 400)


class TestFileAndStatusApi(ApiTestBase):
    def test_file_read_and_traversal_rejected(self):
        ticket = self.data_root / "tickets" / "test.md"
        ticket.write_text("# 工单", encoding="utf-8")
        data = self.call("GET", "/api/file?path=tickets/test.md")["data"]
        self.assertIn("工单", data["content"])

        for bad in ("../outside.md", "D:/x.md", "/etc/passwd", "other/x.md"):
            status, _ = self.call_raw("GET", "/api/file?path=" + urllib.parse.quote(bad))
            self.assertIn(status, (400, 404), f"path={bad!r} 未被拒绝")

    def test_status_includes_backups_and_validation(self):
        self.call("POST", "/api/backup")
        status = self.call("GET", "/api/status")["data"]
        self.assertIsNotNone(status["last_backup"]["path"])
        self.assertTrue(len(status["backups"]) >= 1)
        self.assertIn("name", status["backups"][0])
        self.assertTrue(status["validation_ok"])

    def test_bootstrap(self):
        data = self.call("GET", "/api/bootstrap")["data"]
        self.assertTrue(data["configured"])
        self.assertEqual(data["statuses"][0], "需求确认")

    def test_static_pages_served(self):
        for page in ("/", "/detail.html", "/admin.html", "/css/style.css", "/js/app.js"):
            with urllib.request.urlopen(self.base + page) as r:
                self.assertEqual(r.status, 200, page)

    def test_restore_via_api(self):
        created = self.call("POST", "/api/projects", {"name": "恢复链路测试"})["data"]
        self.call("POST", "/api/backup")
        status = self.call("GET", "/api/status")["data"]
        zip_path = str(Path(status["data_root"]) / status["last_backup"]["path"])
        result = self.call("POST", "/api/restore", {"zip_path": zip_path})["data"]
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["safety_backup"])
        listed = projects.list_projects(result["dest"], include_archived=True)
        self.assertIn(created["id"], [p["id"] for p in listed])


if __name__ == "__main__":
    unittest.main()
