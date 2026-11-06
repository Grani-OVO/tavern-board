# AGENT.md — Agent 读写说明

> 目标读者：任何新开的 Agent（AI 助手）。只读本文档与目标项目文件，即可可靠地创建、更新项目数据。
> 人机共用入口：`python -m app.cli --help`（在本项目代码目录 `app/` 所在根目录执行）。

## 1. 数据布局

数据根目录（首次启动向导选择，运行配置见代码目录下 `app-config.json` 的 `data_root` 字段）：

```
<数据根目录>/
├── manifest.json        # schema_version + last_backup（不存项目清单，列表以扫描 projects/ 为准）
├── projects/
│   └── <project-id>/
│       ├── project.json # 结构化状态（Agent 读写的主入口）
│       └── PROJECT.md   # 项目背景与简介（人读叙事；技术决策与约定只写代码工作区的 PROJECT.md）
├── tickets/             # 工单 Markdown（文件名形如 2026-11-06-<项目名>-<主题>.md）
├── templates/
│   └── project-template.md   # 新建项目时复制为 PROJECT.md 的模板
└── backups/             # 本地 ZIP 快照（自动 + 手动），历史不自动删除
```

## 2. project.json 结构（schema_version: 1）

```json
{
  "schema_version": 1,
  "id": "p-20261106-a3f9c2",
  "name": "项目显示名",
  "status": "开发中",
  "goal": "一句话目标",
  "tech_stack": ["Python 3"],
  "next_step": "当前下一步动作",
  "blocked": null,
  "progress": 40,
  "tags": ["工具"],
  "todos": [{ "id": "t-001", "text": "做某事", "done": false }],
  "tickets": ["tickets/2026-11-06-xxx.md"],
  "created_at": "2026-11-06T13:31:47+08:00",
  "updated_at": "2026-11-06T18:00:00+08:00",
  "archived_at": null
}
```

### 字段规则

| 字段 | 类型 | 规则 |
|---|---|---|
| `schema_version` | int | 必须为 `1` |
| `id` | str | 必填。格式 `p-YYYYMMDD-<6位小写十六进制>`，与所在目录名一致，永不变更 |
| `name` | str | 必填非空。改名不影响关联（关联靠 id） |
| `status` | str | 必填。枚举：`需求确认 / 开发中 / 待验收 / 已验收 / 已上线 / 暂停 / 归档` |
| `goal` | str | 可空 |
| `tech_stack` | str[] | 字符串数组 |
| `next_step` | str | 可空 |
| `blocked` | str \| null | 阻塞说明，`null` 表示无 |
| `progress` | int | 0–100 整数 |
| `tags` | str[] | 字符串数组 |
| `todos` | obj[] | 每项 `{ "id": "t-001", "text": "...", "done": false }`，`done` 为布尔 |
| `tickets` | str[] | 相对数据根目录的路径，**禁止绝对路径、禁止 `..`** |
| `created_at` | str | 必填。ISO 8601 **必须带时区偏移**，如 `2026-11-06T13:31:47+08:00` |
| `updated_at` | str | 必填。每次修改后必须更新 |
| `archived_at` | str \| null | 仅 `status` 为 `归档` 时非空，两者互相绑定 |

### 重要约定

1. **未知字段保留**：加载与写回时不得丢弃、改名、删除任何未知字段。新增字段直接加即可，向后兼容。
2. **写入必须原子**：读 → 校验 → 写同目录临时文件 → `os.replace` 替换。禁止直接覆写原文件。
3. **校验失败不落盘**：合并后校验不通过就放弃写入，原数据保持原样。
4. **UTF-8**：所有 JSON/Markdown 均为 UTF-8，中文不转义（`ensure_ascii=False`）。
5. **结构化状态只存 project.json**；本目录的 `PROJECT.md` 只写项目背景与简介。**技术决策与约定只写项目工作区的 `PROJECT.md`**（代码仓库或工作区根目录那份），两处不双写，避免漂移。
6. **列表以扫描为准**：项目清单不要维护缓存索引，直接扫描 `projects/` 下的目录。
7. **单写入者**：同一项目同一时刻只允许一个写入者（网页手工编辑也算一个写入者）；有人正在开发的项目，不要同时用网页改它的状态。

## 3. 推荐操作方式

### 方式 A：命令行（推荐，自带校验与原子写）

在本项目代码根目录（含 `app/` 的目录）执行：

```bash
# 全库校验（Agent 每次写入前建议跑一次）
python -m app.cli validate [--data-root <数据根目录>]

# 手动备份
python -m app.cli backup [--data-root <数据根目录>]

# 从备份恢复到旁路新目录（不覆盖现有数据）
python -m app.cli restore <zip路径> [--dest-parent <目录>]
```

服务运行时（`python -m app.cli serve`），也可走 HTTP API（`127.0.0.1:8765`）：

- 项目：`GET/POST /api/projects`、`GET/PATCH /api/projects/<id>`、`POST /api/projects/<id>/archive|restore`
- 档案：`GET /api/projects/<id>/markdown`、`POST /api/projects/<id>/markdown`（保存 PROJECT.md，原子写）
- 运维：`GET /api/health`（探活，无需配置）、`GET /api/bootstrap`（配置状态与状态枚举）、`GET /api/status`（数据根、项目数、最近备份、备份列表、校验错误明细）、`POST /api/backup`、`POST /api/restore`

PATCH 请求包含未知字段会返回 400 错误（不是静默忽略），这是故意的。

### 方式 B：直接改文件（服务未运行时）

1. 读取 `projects/<id>/project.json`；
2. 修改目标字段（保留其他所有字段，包括未知字段）；
3. 运行 `python -m app.cli validate` 确认无误；
4. 原子写回（临时文件 + 替换）。

### 手工新建项目（不经过 CLI/API 时）

1. 生成 id：`p-` + 今天日期 `YYYYMMDD` + 6 位随机十六进制；
2. 创建目录 `projects/<id>/`；
3. 写入符合上述 schema 的 `project.json`（`progress` 从 0 开始、`archived_at` 为 null）；
4. 复制 `templates/project-template.md` 为该目录下 `PROJECT.md` 并填写；
5. 运行 `validate` 确认。

## 4. 禁止事项

- 不得在数据包、工单、PROJECT.md、日志中写入 API Key、Token、密码、私钥等敏感信息；
- 不得使用绝对路径写入 `tickets`、`last_backup.path` 等数据包内部字段；
- 不得覆盖恢复：恢复一律解压到旁路新目录；
- 不得把「本地备份完成」表述为「已上传网盘」；
- 不得同时修改 `status` 为 `归档` 而不设 `archived_at`（反之亦然）。
