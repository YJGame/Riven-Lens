# Build clean, distributable Windows packages for 裂罅透镜 (Riven Lens):
#   1. release\裂罅透镜_Windows_安装包.exe  (7-Zip GUI self-extracting LZMA2 installer)
#   2. release\裂罅透镜_Windows_绿色版.zip  (Clean portable ZIP, friendly to WeChat/QQ/Discord/Email)
#   3. release\裂罅透镜_Setup_v3.0.0.exe    (Full Inno Setup Wizard, auto-built if ISCC.exe is installed)

$ErrorActionPreference = "Stop"

$Root = "d:\Antigravity"
$DistSrc = Join-Path $Root "dist\裂罅透镜"
$StageRoot = Join-Path $Root "build\_win_release_stage"
$StageApp = Join-Path $StageRoot "裂罅透镜"
$ReleaseDir = Join-Path $Root "release"

if (-not (Test-Path (Join-Path $DistSrc "裂罅透镜.exe"))) {
    throw "Missing built binary at $DistSrc\裂罅透镜.exe. Build with PyInstaller first."
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageApp | Out-Null

Write-Output "[1/4] Staging clean distribution copy (stripping personal settings/logs/ROI)..."
Copy-Item -Path (Join-Path $DistSrc "*") -Destination $StageApp -Recurse -Force

# Strip personal runtime files so recipients get a clean first-run experience with User Manual popup
foreach ($relDrop in @(
    "裂罅透镜.log",
    "_internal\data\user_settings.json",
    "_internal\data\roi_profiles.json",
    "_internal\data\ocr_models"
)) {
    $p = Join-Path $StageApp $relDrop
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

# Add helper launchers & quick-start guide inside the distributed folder
$adminBat = @'
@echo off
chcp 65001 >nul
setlocal
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:APP_DIR.TrimEnd('\'); Start-Process -FilePath (Join-Path $d '裂罅透镜.exe') -WorkingDirectory $d -Verb RunAs"
'@
[System.IO.File]::WriteAllText((Join-Path $StageApp "启动悬浮窗_管理员.bat"), $adminBat, [System.Text.UTF8Encoding]::new($false))

$shortcutBat = @'
@echo off
chcp 65001 >nul
setlocal
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d = $env:APP_DIR.TrimEnd('\'); " ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$app = Join-Path $d '裂罅透镜.exe'; " ^
  "$desk = [Environment]::GetFolderPath('Desktop'); " ^
  "$s1 = $ws.CreateShortcut((Join-Path $desk '裂罅透镜.lnk')); " ^
  "$s1.TargetPath = $app; $s1.WorkingDirectory = $d; $s1.IconLocation = $app; $s1.Save(); " ^
  "$sm = Join-Path ([Environment]::GetFolderPath('Programs')) '裂罅透镜'; " ^
  "New-Item -ItemType Directory -Force -Path $sm | Out-Null; " ^
  "$s2 = $ws.CreateShortcut((Join-Path $sm '裂罅透镜.lnk')); " ^
  "$s2.TargetPath = $app; $s2.WorkingDirectory = $d; $s2.IconLocation = $app; $s2.Save(); " ^
  "Write-Host '已成功创建桌面与开始菜单快捷方式！' -ForegroundColor Green"
pause
'@
[System.IO.File]::WriteAllText((Join-Path $StageApp "创建桌面快捷方式.bat"), $shortcutBat, [System.Text.UTF8Encoding]::new($false))

$readmeTxt = @'
裂罅透镜（Riven Lens）Windows 版快速上手说明
================================================

1. 启动方式
   - 普通启动：直接双击「裂罅透镜.exe」
   - 管理员启动：如果《星际战甲》或 WeGame 以管理员权限运行，请双击「启动悬浮窗_管理员.bat」
   - 创建快捷方式：双击「创建桌面快捷方式.bat」可一键添加到桌面和开始菜单

2. 首次启动
   - 首次打开会自动弹出《用户手册》，之后随时可按 Ctrl+Alt+D 打开设置面板，点击「用户手册」回看。
   - 请在游戏设置中将显示模式设为「无边框全屏（Borderless Fullscreen）」或窗口化。
   - 建议先点击设置面板的「校准识别框」，按提示对齐单卡、洗卡双卡、聊天紫卡和开核桃区域。

3. 默认快捷键（可在设置面板自定义）
   - F8          ：轮询切换模式（单卡自动 -> 洗卡双卡 -> 聊天F9 -> 开核桃F9 -> 暂停）
   - F9          ：单卡/双卡下查询 Warframe.market；聊天/开核桃下触发识别
   - ESC         ：收起悬浮窗为待命胶囊（保留当前模式）
   - Ctrl+Alt+D  ：打开 / 隐藏设置与控制面板
   - Ctrl+Alt+S  ：导出紫卡精美展示图 PNG 并复制到剪贴板
   - Ctrl+Alt+L  ：锁定 / 解锁防误触热键

4. 退出程序
   - 关闭设置窗口会自动最小化到右下角系统托盘，右键托盘图标选择「退出软件」即可彻底退出。
'@
[System.IO.File]::WriteAllText((Join-Path $StageApp "使用说明.txt"), $readmeTxt, [System.Text.UTF8Encoding]::new($true))

$sevenZip = "C:\Program Files\7-Zip\7z.exe"
$sfxMod = "C:\Program Files\7-Zip\7z.sfx"
$sfxOut = Join-Path $ReleaseDir "裂罅透镜_Windows_安装包.exe"
$zipOut = Join-Path $ReleaseDir "裂罅透镜_Windows_绿色版.zip"
$tmp7z = Join-Path $StageRoot "payload.7z"

if (Test-Path $sfxOut) { Remove-Item $sfxOut -Force }
if (Test-Path $zipOut) { Remove-Item $zipOut -Force }

if ((Test-Path $sevenZip) -and (Test-Path $sfxMod)) {
    Write-Output "[2/4] Building LZMA2 7-Zip GUI Self-Extracting Installer (.exe)..."
    Push-Location $StageRoot
    try {
        & $sevenZip a -t7z -m0=lzma2 -mx=7 -mmt=on -ms=on $tmp7z "裂罅透镜" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z compression failed ($LASTEXITCODE)" }
        # Combine official 7z.sfx GUI module + LZMA2 payload cleanly via binary streams
        $outFs = [System.IO.File]::Create($sfxOut)
        try {
            $bSfx = [System.IO.File]::ReadAllBytes($sfxMod)
            $outFs.Write($bSfx, 0, $bSfx.Length)
            $inFs = [System.IO.File]::OpenRead($tmp7z)
            try { $inFs.CopyTo($outFs) } finally { $inFs.Close() }
        } finally {
            $outFs.Close()
        }
        Remove-Item $tmp7z -Force -ErrorAction SilentlyContinue

        Write-Output "[3/4] Building Portable ZIP archive (chat-app & email friendly)..."
        & $sevenZip a -tzip -mx=5 -mmt=on $zipOut "裂罅透镜" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z zip build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
} else {
    Write-Output "[2/4] 7-Zip not found; building Portable ZIP via Compress-Archive..."
    Compress-Archive -Path $StageApp -DestinationPath $zipOut -Force
}

# Optional Step 4: Compile Inno Setup 6 Wizard if ISCC.exe is installed
$iscc = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    Write-Output "[4/4] Inno Setup 6 detected ($iscc) -> compiling Setup Wizard..."
    & $iscc (Join-Path $Root "installer\RivenLens.iss")
} else {
    Write-Output "[4/4] Inno Setup 6 not installed (skipped Setup Wizard; installer\RivenLens.iss ready for future use)."
}

# Clean up staging folder
Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Output ""
Write-Output "=== Windows Distributable Packages Ready ==="
Get-ChildItem $ReleaseDir -Filter "裂罅透镜_Windows_*" | Select-Object FullName, @{N="SizeMB";E={[math]::Round($_.Length/1MB, 1)}}, LastWriteTime
