@echo off
chcp 65001 >nul
title 股票分析APP - 启动

cd /d D:\gupiao\gpfxapp

:: 设置镜像和路径
set FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn
set PUB_HOSTED_URL=https://pub.flutter-io.cn
set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr
set ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
set ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk
set PATH=%PATH%;%JAVA_HOME%\bin;%ANDROID_HOME%\platform-tools;C:\src\flutter\bin

echo ========================================
echo   股票分析APP v1.0
echo   天空蓝主题  |  红涨绿跌
echo ========================================
echo.

:: 检查Flutter
where flutter >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到Flutter
    echo 请从 https://flutter.cn 下载并解压到 C:\src\flutter
    pause
    exit /b 1
)

:: 安装依赖
echo [1/3] 安装依赖包...
call flutter pub get
echo.

:: 检查设备
echo [2/3] 检查可用设备...
call flutter devices
echo.

:: 选择运行方式
echo [3/3] 选择运行方式:
echo   1. 连接安卓手机运行
echo   2. 启动模拟器运行
echo   3. 仅构建APK（生成APK文件）
echo.
set /p choice="请选择 (1/2/3): "

if "%choice%"=="1" goto run_device
if "%choice%"=="2" goto run_emulator
if "%choice%"=="3" goto build_apk
goto end

:run_device
echo.
echo 启动APP到连接的设备...
call flutter run
goto end

:run_emulator
echo.
echo 启动模拟器...
call flutter emulators --launch <emulator_id>
call flutter run
goto end

:build_apk
echo.
echo 构建APK...
call flutter build apk --debug
echo.
echo APK生成位置: build\app\outputs\flutter-apk\app-debug.apk
echo 将此APK发送到手机安装即可使用
goto end

:end
echo.
echo 按任意键退出...
pause >nul
