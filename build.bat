@echo off
chcp 65001 >nul
REM 一键打包：把 GUI 打包成 Windows exe（onedir 模式 + 打 zip 便于分发）
REM 依赖：已安装 PyInstaller（python -m pip install pyinstaller）
REM 产物：dist\书到了.zip（解压后双击 书到了.exe 即可运行）

cd /d "%~dp0"

echo [1/4] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/4] 生成图标（若无 app.ico）...
if not exist app.ico python make_icon.py

echo [3/4] 开始打包（首次较慢，请耐心等待）...
python -m PyInstaller build.spec --noconfirm
if errorlevel 1 (
    echo 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo [4/4] 打 zip 压缩包便于分发...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\书到了\*' -DestinationPath 'dist\书到了.zip' -Force"

echo.
echo 打包完成！
echo   可执行文件夹: dist\书到了\
echo   分发压缩包:   dist\书到了.zip
echo.
echo 分发时把 zip 发给别人，解压后双击 书到了.exe 即可。
pause
