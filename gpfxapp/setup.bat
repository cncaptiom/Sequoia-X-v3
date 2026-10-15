@echo off
chcp 65001 >nul
title 股票分析APP - 环境配置

echo ========================================
echo   股票分析APP - 环境自动配置脚本
echo ========================================
echo.

:: 配置国内镜像
echo [1/4] 设置国内镜像环境变量...
setx FLUTTER_STORAGE_BASE_URL "https://storage.flutter-io.cn" /M >nul 2>&1
setx PUB_HOSTED_URL "https://pub.flutter-io.cn" /M >nul 2>&1
echo 完成

:: 设置JAVA和Android SDK环境变量
echo [2/4] 设置Java和Android SDK环境变量...
setx JAVA_HOME "C:\Program Files\Android\Android Studio\jbr" /M >nul 2>&1
setx ANDROID_HOME "%LOCALAPPDATA%\Android\Sdk" /M >nul 2>&1
echo 完成

:: 下载Flutter SDK
echo [3/4] 下载Flutter SDK (约900MB，请耐心等待)...
echo 正在从 flutter-io.cn 下载...
powershell -Command "& {
    $url = 'https://storage.flutter-io.cn/flutter_infra_release/releases/stable/windows/flutter_windows_3.41.7-stable.zip'
    $output = 'C:\src\flutter.zip'
    Write-Host '下载中: 0%'
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($url, $output)
    Write-Host '下载完成'
}"
echo 完成

:: 解压Flutter SDK
echo [4/4] 解压Flutter SDK...
powershell -Command "& {
    Expand-Archive -Path 'C:\src\flutter.zip' -DestinationPath 'C:\src\' -Force
    Write-Host '解压完成'
}"

:: 添加到PATH
setx PATH "%PATH%;C:\src\flutter\bin" /M >nul 2>&1

echo.
echo ========================================
echo   配置完成!
echo ========================================
echo.
echo 请关闭此窗口，重新打开终端后执行:
echo.
echo   cd /d D:\gupiao\Sequoia-X-v3\gpfxapp
echo   flutter create . --project-name gpfxapp
echo   flutter pub get
echo   flutter run
echo.
echo 或者直接运行 run_app.bat
echo.
pause
