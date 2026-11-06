# 个人项目管理系统

简单、易用、可拓展、Agent 可读写的本地个人项目管理看板。日式奇幻酒馆公告栏风格。

- 需求与验收标准：`D:\WorkBuddy\工单\2026-11-06-个人项目管理系统-MVP.md`（定稿冻结）
- Agent 读写说明：[AGENT.md](AGENT.md)
- 项目状态：`PROJECT.md`

## 运行

前置条件：Python 3.9+（仅标准库，无需安装任何第三方依赖）。

```bash
# 1. 首次初始化（也可以启动后在网页向导里完成）
python -m app.cli init "D:\你的数据目录"

# 2. 启动本地服务（仅监听 127.0.0.1，不对外）
python -m app.cli serve            # 默认端口 8765
python -m app.cli serve --port 9000
```

浏览器打开 `http://127.0.0.1:8765/`。

## 常用命令

```bash
python -m app.cli validate         # 全库校验（退出码 0=通过，1=有问题）
python -m app.cli backup           # 立即生成 ZIP 备份
python -m app.cli restore <zip>    # 从备份恢复到旁路新目录（不覆盖现有数据）
```

不传 `--data-root` 时自动读取 `app-config.json` 中配置的数据根目录。

## 备份与迁移（Windows ↔ macOS）

数据包内部全部使用相对路径，可跨平台迁移。

**备份（任一台设备）：**

1. 网页「备份与设置」页点「立即备份」，或运行 `python -m app.cli backup`；
2. 得到形如 `backups/pms-backup-20261106-133000.zip` 的文件（页面会显示完整路径）；
3. **手动**上传该 ZIP 到你的百度网盘。本系统不自动连接网盘；页面上「本地备份完成」不等于已上传。

**恢复（另一台设备）：**

1. 新设备装好 Python 3.9+，拷贝本项目代码目录（或 `git clone`），无需安装依赖；
2. 从网盘下载备份 ZIP 到本地；
3. 启动服务 `python -m app.cli serve`，首次启动向导选「从备份恢复」，填入 ZIP 路径；
   或命令行：`python -m app.cli restore "下载的.zip" --dest-parent "目标父目录"`；
4. 恢复过程：系统先对当前数据做安全快照 → 解压到旁路新目录 `pms-restored-<时间戳>` → 全库校验；
5. 校验通过后确认切换：`python -m app.cli init "<恢复出的目录>"`（或网页上点「切换到此目录」）。

任何情况下恢复都不会覆盖现有数据目录；旧目录与恢复前的安全快照始终保留。

## 注意事项

- 同一时间只在一台设备修改数据；第一版不支持多设备并发合并；
- 备份不含 `.env`、密钥、`backups/` 历史快照与临时文件；
- 数据校验发现问题（JSON 损坏、非法状态等）时，先运行 `python -m app.cli validate` 查看报告；
- 服务仅监听 `127.0.0.1`，不要暴露到局域网。

## 目录结构

```
app/            Python 标准库实现的本地服务与核心模块
  core/         schema 校验 / 原子存储 / 项目读写 / 备份恢复 / 运行配置
  static/       看板、详情、备份设置三页前端（原生 HTML/CSS/JS）
tests/          unittest 测试
AGENT.md        Agent 直接读写数据源的操作规范
PROJECT.md      项目状态卡
```
