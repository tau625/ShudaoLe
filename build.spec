# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：把 GUI 打包成 Windows exe（onedir 模式）
#
# 用法（在本目录执行）：
#   python -m PyInstaller build.spec
#
# 产物：
#   dist/书到了/书到了.exe（连同运行时依赖在同一文件夹）
#
# 说明：
#   - onedir（非 onefile）：启动不涉及"自解压到临时目录"，大幅降低杀软误报，
#     且启动更快。分发时把整个 dist/书到了 文件夹打成 zip 即可。
#   - 无控制台窗口（console=False）：双击不会弹出黑窗
#   - 令牌抓取已整合进 GUI 进程内（shudaole.token 模块），随 exe 自动收集，
#     对方机器无需安装 Python，真正零依赖。
#   - 隐藏导入 requests/urllib3，确保 requests 被完整收集
#   - icon：app.ico（朱砂印「書」，由 AI 生成图 png_to_ico 转制）
#   - version 文件：加入版本/版权信息，进一步降低杀软误报、提升信任度

import sys

# version 文件与 icon 仅在 Windows 上生效；macOS/Linux 构建时由 CI 传 None，
# 保证同一份 spec 三平台都能跑
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# 收集版本信息文件（打包时由 build.bat 先生成 version_info.txt，这里引用）
_vi = None
_vi_path = 'version_info.txt'
try:
    with open(_vi_path, 'r', encoding='utf-8') as f:
        _vi = f.read()
except OSError:
    pass

block_cipher = None

a = Analysis(
    ['smartedu_downloader_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('smartedu_webui.html', '.'),
        # 版本号单一来源：界面显示与 exe 文件属性都从它解析，发版只改这一处
        ('version_info.txt', '.'),
        # PolyForm 许可的 Notices 条款要求分发时附带许可声明
        ('LICENSE', '.'),
    ],
    hiddenimports=[
        'requests',
        'urllib3',
        'urllib3.util.retry',
        'urllib3.poolmanager',
        'urllib3.connection',
        'shudaole.token',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 去掉用不到的重量级库，减小体积
        'tqdm',
        'numpy',
        'pandas',
        'matplotlib',
        'PIL',
        'tkinter',
        'unittest',
        'pydoc',
        'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir：EXE 生成到 dist/<name>/ 文件夹内，runtime 依赖（python*.dll 等）一并放入
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,        # onedir：二进制单独放，不进 exe
    name='书到了',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico' if IS_WIN else None,              # 程序图标
    version=_vi_path if (_vi and IS_WIN) else None,  # 版本信息（Windows 文件属性）——version 参数是文件路径
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='书到了',
)

# macOS：把 COLLECT 产物包成原生 .app（Finder 双击启动、Dock 图标、无终端窗口），
# CI 再用 hdiutil 打成 .dmg 分发。仅在 macOS 构建时生效。
if IS_MAC:
    bundle = BUNDLE(
        coll,
        name='书到了',
        icon='assets/app.icns',
        bundle_identifier='cn.tau625.shudaole',
    )
