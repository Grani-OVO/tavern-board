"""test_schema.py — 校验规则单元测试。"""
import unittest

from app.core import schema


def valid_project(**overrides):
    base = {
        "schema_version": 1,
        "id": "p-20261106-abcdef",
        "name": "测试项目",
        "status": "开发中",
        "goal": "目标",
        "tech_stack": ["Python"],
        "next_step": "写测试",
        "blocked": None,
        "progress": 10,
        "tags": ["工具"],
        "todos": [{"id": "t-001", "text": "做事", "done": False}],
        "tickets": ["tickets/2026-11-06-x.md"],
        "created_at": "2026-11-06T13:31:47+08:00",
        "updated_at": "2026-11-06T13:31:47+08:00",
        "archived_at": None,
    }
    base.update(overrides)
    return base


class TestValidateProject(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(schema.validate_project(valid_project()), [])

    def test_unknown_fields_kept_and_not_rejected(self):
        data = valid_project(custom_field={"any": "thing"})
        self.assertEqual(schema.validate_project(data), [])

    def test_missing_required(self):
        data = valid_project()
        del data["name"]
        errors = schema.validate_project(data)
        self.assertTrue(any("name" in e for e in errors))

    def test_bad_status_enum(self):
        errors = schema.validate_project(valid_project(status="进行中"))
        self.assertTrue(any("status" in e for e in errors))

    def test_bad_schema_version(self):
        errors = schema.validate_project(valid_project(schema_version=2))
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_bad_id_format(self):
        errors = schema.validate_project(valid_project(id="project-1"))
        self.assertTrue(any("id" in e for e in errors))

    def test_progress_out_of_range(self):
        for value in (-1, 101, "50", True, 50.5):
            errors = schema.validate_project(valid_project(progress=value))
            self.assertTrue(any("progress" in e for e in errors), f"progress={value!r} 未被拒绝")

    def test_datetime_missing_tz(self):
        errors = schema.validate_project(valid_project(updated_at="2026-11-06T13:31:47"))
        self.assertTrue(any("updated_at" in e and "时区" in e for e in errors))

    def test_datetime_invalid(self):
        errors = schema.validate_project(valid_project(created_at="not-a-date"))
        self.assertTrue(any("created_at" in e for e in errors))

    def test_absolute_path_ticket(self):
        for bad in ("D:\\WorkBuddy\\a.md", "/etc/passwd", "../outside.md"):
            errors = schema.validate_project(valid_project(tickets=[bad]))
            self.assertTrue(any("tickets[0]" in e for e in errors), f"tickets={bad!r} 未被拒绝")

    def test_archive_binding(self):
        errors = schema.validate_project(valid_project(status="归档"))
        self.assertTrue(any("archived_at" in e for e in errors))
        errors = schema.validate_project(valid_project(archived_at="2026-11-06T13:31:47+08:00"))
        self.assertTrue(any("archived_at" in e for e in errors))

    def test_blocked_must_be_str_or_null(self):
        errors = schema.validate_project(valid_project(blocked=5))
        self.assertTrue(any("blocked" in e for e in errors))

    def test_todos_structure(self):
        errors = schema.validate_project(valid_project(todos=[{"id": "t-1", "done": "yes"}]))
        self.assertTrue(any("todos[0].text" in e or "todos[0].done" in e for e in errors))

    def test_string_array_fields(self):
        errors = schema.validate_project(valid_project(tech_stack="Python"))
        self.assertTrue(any("tech_stack" in e for e in errors))
        errors = schema.validate_project(valid_project(tags=[1, 2]))
        self.assertTrue(any("tags" in e for e in errors))

    def test_updateable_fields_do_not_include_immutable(self):
        for field in ("id", "schema_version", "created_at"):
            self.assertNotIn(field, schema.UPDATEABLE_FIELDS)
        for field in ("name", "status", "progress", "todos", "tickets"):
            self.assertIn(field, schema.UPDATEABLE_FIELDS)


class TestValidateManifest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(
            schema.validate_manifest(
                {"schema_version": 1, "last_backup": {"time": "2026-11-06T13:00:00+08:00",
                                                      "path": "backups/x.zip"}}),
            [])

    def test_absolute_backup_path_rejected(self):
        errors = schema.validate_manifest(
            {"schema_version": 1, "last_backup": {"time": None, "path": "D:/backups/x.zip"}})
        self.assertTrue(any("last_backup.path" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
