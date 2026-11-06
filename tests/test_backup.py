"""test_backup.py — 备份打包、内容排除、恢复、校验、自动节流。"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core import backup, projects


class BackupTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        projects.init_data_root(self.data_root)
        self.data = projects.create_project(self.data_root, name="备份测试项目",
                                            goal="验证备份恢复")
        ticket = self.data_root / "tickets" / "2026-11-06-备份测试.md"
        ticket.parent.mkdir(exist_ok=True)
        ticket.write_text("# 工单\n\n内容", encoding="utf-8")
        projects.update_project(self.data_root, self.data["id"],
                                {"tickets": ["tickets/2026-11-06-备份测试.md"]})

    def tearDown(self):
        self.tmp.cleanup()


class TestCreateBackup(BackupTestBase):
    def test_zip_contains_expected_and_excludes_junk(self):
        # 制造应被排除的文件
        (self.data_root / ".env").write_text("SECRET=1", encoding="utf-8")
        cache = self.data_root / "projects" / self.data["id"] / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"\x00\x01")
        (self.data_root / "projects" / self.data["id"] / "project.json.tmp").write_text("tmp", encoding="utf-8")

        zip_path = backup.create_backup(self.data_root)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        self.assertIn(f"projects/{self.data['id']}/project.json", names)
        self.assertIn(f"projects/{self.data['id']}/PROJECT.md", names)
        self.assertIn("tickets/2026-11-06-备份测试.md", names)
        self.assertIn("templates/project-template.md", names)
        self.assertIn("manifest.json", names)

        self.assertFalse(any(n.startswith("backups/") for n in names), "历史备份不得嵌套进备份包")
        self.assertFalse(any(".env" in n for n in names), ".env 不得进备份包")
        self.assertFalse(any("__pycache__" in n for n in names))
        self.assertFalse(any(n.endswith(".tmp") or n.endswith(".pyc") for n in names))

    def test_manifest_records_relative_backup_path(self):
        zip_path = backup.create_backup(self.data_root)
        manifest = projects.read_manifest(self.data_root)
        rel = manifest["last_backup"]["path"]
        self.assertIn("backups/", rel)
        self.assertNotIn(str(self.tmp.name), rel)  # 无本机绝对路径
        self.assertEqual((self.data_root / rel).resolve(), zip_path.resolve())

    def test_auto_backup_throttle(self):
        first = backup.auto_backup_if_due(self.data_root)
        self.assertIsNotNone(first, "无备份记录时应当备份")
        second = backup.auto_backup_if_due(self.data_root)
        self.assertIsNone(second, "刚备份过，24h 内不应再自动备份")

    def test_history_backups_kept(self):
        backup.create_backup(self.data_root)
        backup.create_backup(self.data_root)
        backups = list((self.data_root / "backups").glob("*.zip"))
        self.assertEqual(len(backups), 2)


class TestRestore(BackupTestBase):
    def test_restore_to_fresh_directory_keeps_association(self):
        zip_path = backup.create_backup(self.data_root)
        with tempfile.TemporaryDirectory() as dest_parent:
            result = backup.restore_backup(zip_path, Path(dest_parent))
            self.assertEqual(result["errors"], [], result["errors"])
            dest = result["dest"]
            self.assertTrue((dest / "manifest.json").is_file())

            # 项目与工单关联保持正常（相对路径不变）
            pjson = dest / "projects" / self.data["id"] / "project.json"
            data = json.loads(pjson.read_text(encoding="utf-8"))
            self.assertEqual(data["tickets"], ["tickets/2026-11-06-备份测试.md"])
            self.assertTrue((dest / "tickets" / "2026-11-06-备份测试.md").is_file())

            # 恢复后的目录可以直接被 list_projects 读取
            listed = projects.list_projects(dest)
            self.assertEqual([p["id"] for p in listed], [self.data["id"]])

            # last_backup 被重置（包内无历史备份）
            manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertIsNone(manifest["last_backup"]["path"])

    def test_restore_of_fresh_backup_has_no_stale_backup_error(self):
        """刚生成的备份包内 manifest 记录的备份文件不在包里，恢复不得因此报错。"""
        backup.create_backup(self.data_root)  # manifest 现在指向这个最新 zip
        zip_path = sorted((self.data_root / "backups").glob("*.zip"))[-1]
        with tempfile.TemporaryDirectory() as dest_parent:
            result = backup.restore_backup(zip_path, Path(dest_parent))
            self.assertEqual(result["errors"], [], result["errors"])

    def test_restore_never_overwrites_source(self):
        zip_path = backup.create_backup(self.data_root)
        marker = self.data_root / "projects" / self.data["id"] / "PROJECT.md"
        before = marker.read_text(encoding="utf-8")
        result = backup.restore_backup(zip_path)  # 默认放备份包旁边
        self.assertEqual(result["errors"], [])
        self.assertNotEqual(result["dest"], self.data_root)
        self.assertEqual(marker.read_text(encoding="utf-8"), before)

    def test_restore_rejects_corrupted_zip(self):
        zip_path = backup.create_backup(self.data_root)
        # 篡改包内 project.json 为非法 JSON
        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr(f"projects/{self.data['id']}/project.json", "{ broken")
        result = backup.restore_backup(zip_path)
        self.assertTrue(result["errors"])

    def test_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil = Path(tmp) / "evil.zip"
            with zipfile.ZipFile(evil, "w") as zf:
                zf.writestr("../escape.txt", "x")
                zf.writestr("manifest.json", "{}")
            result = backup.restore_backup(evil)
            self.assertTrue(any(".." in e for e in result["errors"]))

    def test_restore_requires_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("something.txt", "x")
            result = backup.restore_backup(bad)
            self.assertTrue(any("manifest" in e for e in result["errors"]))

    def test_full_cycle_same_machine(self):
        """端到端：建项目 → 备份 → 删除原目录 → 从备份恢复 → 数据完整。"""
        zip_path = backup.create_backup(self.data_root)
        with tempfile.TemporaryDirectory() as holder:
            moved_zip = Path(holder) / "moved.zip"
            moved_zip.write_bytes(zip_path.read_bytes())
            result = backup.restore_backup(moved_zip)
            self.assertEqual(result["errors"], [])
            listed = projects.list_projects(result["dest"])
            self.assertEqual(listed[0]["name"], "备份测试项目")


if __name__ == "__main__":
    unittest.main()
