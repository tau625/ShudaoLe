#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键打包脚本：清理 -> 生成图标（如缺）-> PyInstaller -> 打分发包。

产物：dist/ShudaoLe-v<版本号>.zip，版本号自动从 version_info.txt 解析，
命名与 GitHub Release 资产一致。Windows/Linux/macOS 均可运行（三平台的
自动构建见 .github/workflows/release.yml）。

说明：
- 逻辑放在 Python 而不是批处理里，因为 cmd 的批处理解析器在文件含多字节
  字符时会错算读取位置、把行拦腰截断（测试实证），纯 ASCII 薄壳 build.bat
  只负责调起本脚本。
- 全程进程内完成（图标导入 make_icon、打包调 PyInstaller 的编程入口），
  不派生子进程、不拼接任何命令字符串。
"""
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR_NAME = "书到了"


def read_version() -> str:
    """从 version_info.txt 解析 FileVersion（版本号单一数据源）"""
    text = (ROOT / "version_info.txt").read_text(encoding="utf-8")
    m = re.search(r"StringStruct\(u'FileVersion',\s*u'([^']+)'", text)
    if not m:
        raise SystemExit("错误：无法从 version_info.txt 解析出版本号")
    return m.group(1)


def main() -> int:
    for d in ("build", "dist"):
        if (ROOT / d).exists():
            print(f"[1/4] 清理旧产物: {d}/")
            shutil.rmtree(ROOT / d)

    if not (ROOT / "app.ico").exists():
        print("[2/4] 生成图标 app.ico")
        import make_icon
        make_icon.main()
    else:
        print("[2/4] 图标已存在，跳过")

    print("[3/4] PyInstaller 打包（首次较慢，请耐心等待）...")
    from PyInstaller.__main__ import run as pyinstaller_run
    try:
        pyinstaller_run(["build.spec", "--noconfirm"])
    except SystemExit as e:
        # PyInstaller 的命令行入口用 SystemExit 上报退出码
        if e.code not in (0, None):
            raise SystemExit(f"PyInstaller 打包失败（退出码 {e.code}）")

    print("[4/4] 打分发包...")
    version = read_version()
    dist = ROOT / "dist"
    out = dist / f"ShudaoLe-v{version}.zip"
    app_dir = dist / APP_DIR_NAME
    if not app_dir.is_dir():
        raise SystemExit(f"错误：打包产物缺失 {app_dir}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(dist))

    print(f"\n打包完成！\n  可执行文件夹: {app_dir}\n  分发压缩包:   {out}")
    print("分发时把 zip 发给别人，解压后即可运行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
