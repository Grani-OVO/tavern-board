"""cli.py — 命令行入口（人可用，Agent 也可用）。

用法：
    python -m app.cli init    <数据根目录绝对路径>
    python -m app.cli serve   [--port 8765]
    python -m app.cli validate [--data-root <路径>]   # 不传则读 app-config
    python -m app.cli backup   [--data-root <路径>]
    python -m app.cli restore  <zip路径> [--dest-parent <目录>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import backup, config, projects


def _resolve_data_root(arg_value: str | None) -> Path:
    if arg_value:
        return Path(arg_value)
    root = config.get_data_root()
    if root is None:
        print("未指定 --data-root，且尚未完成首次启动配置。", file=sys.stderr)
        sys.exit(2)
    return root


def cmd_init(args) -> int:
    data_root = Path(args.data_root)
    projects.init_data_root(data_root)
    config.set_data_root(data_root)
    print(f"数据根目录已初始化并激活: {data_root.resolve()}")
    return 0


def cmd_serve(args) -> int:
    from . import server
    server.run(host="127.0.0.1", port=args.port)
    return 0


def cmd_validate(args) -> int:
    data_root = _resolve_data_root(args.data_root)
    report = projects.validate_all(data_root)
    if report["ok"]:
        print(f"校验通过: {data_root}")
        return 0
    for pid, errors in report["projects"].items():
        for e in errors:
            print(f"ERROR {e}" if e else f"OK [{pid}]")
    for e in report["manifest"]:
        print(f"ERROR manifest: {e}")
    for e in report["other"]:
        print(f"ERROR {e}")
    print(f"校验未通过: {data_root}")
    return 1


def cmd_backup(args) -> int:
    data_root = _resolve_data_root(args.data_root)
    zip_path = backup.create_backup(data_root, reason="manual")
    print(f"备份完成: {zip_path}")
    return 0


def cmd_restore(args) -> int:
    result = backup.restore_backup(Path(args.zip_path),
                                   Path(args.dest_parent) if args.dest_parent else None)
    if result["errors"]:
        for e in result["errors"]:
            print(f"ERROR {e}")
        print("恢复失败：备份包未通过校验，未生成可用目录。")
        return 1
    print(f"恢复完成: {result['dest']}")
    print("如需启用该目录，运行: python -m app.cli init "
          f"\"{result['dest']}\" 或在网页向导中切换。")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pms", description="个人项目管理系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化并激活数据根目录")
    p.add_argument("data_root")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("serve", help="启动本地网页服务（仅 127.0.0.1）")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("validate", help="全库校验")
    p.add_argument("--data-root", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("backup", help="立即生成 ZIP 备份")
    p.add_argument("--data-root", default=None)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="从 ZIP 恢复到旁路新目录")
    p.add_argument("zip")
    p.add_argument("--dest-parent", default=None)
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
