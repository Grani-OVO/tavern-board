"""test_projects.py — 项目读写、原子更新、未知字段保留、筛选、归档、全库校验。"""
import json
import tempfile
import unittest
from pathlib import Path

from app.core import projects, schema


class ProjectTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        projects.init_data_root(self.data_root)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, name="示例项目", **kwargs):
        return projects.create_project(self.data_root, name=name, **kwargs)


class TestCreateAndRead(ProjectTestBase):
    def test_create_generates_files_and_dirs(self):
        data = self.create()
        pdir = self.data_root / "projects" / data["id"]
        self.assertTrue((pdir / "project.json").is_file())
        self.assertTrue((pdir / "PROJECT.md").is_file())
        on_disk = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["id"], data["id"])
        self.assertEqual(schema.validate_project(on_disk), [])

    def test_project_md_from_template(self):
        data = self.create(name="酒馆改造", goal="翻新柜台")
        md = (self.data_root / "projects" / data["id"] / "PROJECT.md").read_text(encoding="utf-8")
        self.assertIn("酒馆改造", md)
        self.assertIn("翻新柜台", md)

    def test_id_format(self):
        data = self.create()
        self.assertRegex(data["id"], r"^p-\d{8}-[0-9a-f]{6}$")

    def test_create_duplicate_id_rejected(self):
        data = self.create()
        with self.assertRaises(ValueError):
            projects.create_project(self.data_root, name="重名", project_id=data["id"])

    def test_init_data_root_is_idempotent(self):
        projects.init_data_root(self.data_root)
        self.assertTrue((self.data_root / "manifest.json").is_file())


class TestUpdate(ProjectTestBase):
    def test_update_writes_and_refreshes_timestamp(self):
        data = self.create()
        updated = projects.update_project(self.data_root, data["id"], {"status": "待验收", "progress": 55})
        self.assertEqual(updated["status"], "待验收")
        on_disk = json.loads(
            (self.data_root / "projects" / data["id"] / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["progress"], 55)
        self.assertGreaterEqual(on_disk["updated_at"], data["updated_at"])

    def test_unknown_fields_in_file_are_preserved(self):
        """Agent 自行扩展的字段，网页更新后必须原样保留。"""
        data = self.create()
        pdir = self.data_root / "projects" / data["id"]
        raw = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        raw["custom_agent_field"] = {"keep": "me"}
        (pdir / "project.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        projects.update_project(self.data_root, data["id"], {"next_step": "新步骤"})
        after = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(after["custom_agent_field"], {"keep": "me"})
        self.assertEqual(after["next_step"], "新步骤")

    def test_patch_unknown_field_rejected(self):
        """PATCH 带未知字段必须报错，不允许静默忽略或删除。"""
        data = self.create()
        with self.assertRaises(ValueError) as ctx:
            projects.update_project(self.data_root, data["id"], {"secret_field": 1})
        self.assertIn("secret_field", str(ctx.exception))
        on_disk = json.loads(
            (self.data_root / "projects" / data["id"] / "project.json").read_text(encoding="utf-8"))
        self.assertNotIn("secret_field", on_disk)

    def test_invalid_update_does_not_touch_disk(self):
        data = self.create()
        pjson = self.data_root / "projects" / data["id"] / "project.json"
        before = pjson.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            projects.update_project(self.data_root, data["id"], {"status": "不存在的状态"})
        self.assertEqual(pjson.read_text(encoding="utf-8"), before)

    def test_immutable_fields_rejected(self):
        data = self.create()
        with self.assertRaises(ValueError):
            projects.update_project(self.data_root, data["id"], {"id": "p-20260101-000000"})


class TestArchiveFilterPersist(ProjectTestBase):
    def test_archive_then_hidden_and_restorable(self):
        data = self.create()
        projects.archive_project(self.data_root, data["id"])
        active = projects.list_projects(self.data_root)
        self.assertEqual([p["id"] for p in active], [])
        archived = projects.list_archived(self.data_root)
        self.assertEqual([p["id"] for p in archived], [data["id"]])
        self.assertIsNotNone(archived[0]["archived_at"])

        projects.restore_project(self.data_root, data["id"], status="需求确认")
        self.assertEqual(len(projects.list_projects(self.data_root)), 1)

    def test_filter_by_status_tag_query(self):
        a = self.create(name="甲项目", tags=["前端"])
        self.create(name="乙项目", tags=["后端"], status="暂停")
        self.assertEqual([p["id"] for p in projects.list_projects(self.data_root, tag="前端")], [a["id"]])
        self.assertEqual(len(projects.list_projects(self.data_root, status="暂停")), 1)
        self.assertEqual(len(projects.list_projects(self.data_root, q="甲项目")), 1)
        self.assertEqual(len(projects.list_projects(self.data_root, q="不存在的东西")), 0)

    def test_persistence_across_reload(self):
        """关闭再重启（重新扫描目录）后数据仍在。"""
        data = self.create(name="持久项目")
        projects.update_project(self.data_root, data["id"], {"progress": 77})
        # 重新加载 == 重启应用
        again = projects.list_projects(self.data_root)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["name"], "持久项目")
        self.assertEqual(again[0]["progress"], 77)


class TestValidateAll(ProjectTestBase):
    def test_ok_when_clean(self):
        self.create()
        report = projects.validate_all(self.data_root)
        self.assertTrue(report["ok"], report)

    def test_detects_corrupted_json(self):
        data = self.create()
        pjson = self.data_root / "projects" / data["id"] / "project.json"
        pjson.write_text("{ broken json !!", encoding="utf-8")
        report = projects.validate_all(self.data_root)
        self.assertFalse(report["ok"])
        self.assertTrue(any("无法读取" in e for e in report["projects"][data["id"]]))

    def test_detects_illegal_status(self):
        data = self.create()
        pjson = self.data_root / "projects" / data["id"] / "project.json"
        raw = json.loads(pjson.read_text(encoding="utf-8"))
        raw["status"] = "神秘阶段"
        pjson.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        report = projects.validate_all(self.data_root)
        self.assertFalse(report["ok"])

    def test_detects_id_dir_mismatch(self):
        data = self.create()
        pjson = self.data_root / "projects" / data["id"] / "project.json"
        raw = json.loads(pjson.read_text(encoding="utf-8"))
        raw["id"] = "p-20260101-aaaaaa"
        pjson.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        report = projects.validate_all(self.data_root)
        self.assertFalse(report["ok"])
        self.assertTrue(any("目录名不一致" in e for e in report["projects"][data["id"]]))

    def test_detects_missing_backup_file(self):
        manifest = projects.read_manifest(self.data_root)
        manifest["last_backup"] = {"time": "2026-11-06T10:00:00+08:00",
                                   "path": "backups/ghost.zip"}
        projects.write_manifest(self.data_root, manifest)
        report = projects.validate_all(self.data_root)
        self.assertFalse(report["ok"])
        self.assertTrue(any("不存在" in e for e in report["manifest"]))


if __name__ == "__main__":
    unittest.main()
