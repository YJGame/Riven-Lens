# 裂罅透镜（Riven Lens）Linux Wayland 适配指南

本版本与 Windows 版共享同一套核心源码，完整保留全部功能：
- 单张紫卡 / 洗卡双卡对比（含 ≥90% 熔接复合词条、≥98% 流光边框、双卡无锁定 S 级提示音）
- 聊天链接紫卡 + Warframe.market 精准查价
- 开核桃 4 选 1 极简比价条（金色首选/银色次选徽章、白金/杜卡图标、实时 WFCD 入库锁头）
- 全 15 种《星际战甲》官方语言 + 按需自动拉取 RapidOCR 模型
- 6 套配色主题 + 4 槽位自由选色（同时作用于设置面板与悬浮窗）+ 边缘磁吸对齐
- 首次启动用户手册 + 设置面板「用户手册」入口

---

## 一、系统依赖准备（按桌面环境安装）

### 1. Arch Linux / EndeavourOS / CachyOS（Hyprland / Sway / KDE）
```bash
# Hyprland / Sway (wlroots):
sudo pacman -S python python-pip grim pipewire noto-fonts-cjk

# KDE Plasma Wayland:
sudo pacman -S python python-pip spectacle pipewire noto-fonts-cjk
# （可选）KDE 切出游戏自动隐藏支持：安装 AUR 中的 kdotool
```

### 2. Fedora / Ubuntu / Debian（KDE / GNOME）
```bash
# Fedora (KDE / GNOME):
sudo dnf install python3 python3-pip pipewire-utils google-noto-sans-cjk-fonts spectacle
# 或 wlroots: sudo dnf install grim

# Ubuntu 24.04+ (GNOME / KDE):
sudo apt install python3 python3-venv python3-pip pipewire-bin fonts-noto-cjk gnome-screenshot
```

---

## 二、快速启动或一键打包

```bash
# 方式 A：构建独立可执行目录（推荐，自动创建虚拟环境 + 跑单测 + 打包）
bash linux/build_linux.sh

# 方式 B：直接运行（智能自动适配 Wayland 截屏与悬浮窗显示）
bash linux/run_wayland.sh
```

> 可写配置目录自动存于 `~/.config/WarframeRivenOverlay/data/`（含 `user_settings.json`、`roi_profiles.json`、`prime_prices.json`），OCR 语言包存于 `~/.config/WarframeRivenOverlay/ocr_models/`。

---

## 三、Wayland 全局快捷键两种方案（任选其一）

Wayland 安全协议默认禁止普通窗口偷听其他应用的全局按键，因此本工具内置**两种完整方案**：

### 方案 1：加入 `input` 组（零配置，最接近 Windows 体验，含非拦截 ESC）
```bash
sudo usermod -aG input "$USER"
# 注销并重新登录桌面后生效
```
生效后软件会自动启用内置 `evdev` 非拦截监听，`F8` / `F9` / `ESC` / `Ctrl+Alt+D` / `Ctrl+Alt+S` / `Ctrl+Alt+L` 以及设置面板自定义快捷键全部开箱即用，且绝不吞掉游戏内 `ESC`。

### 方案 2：合成器原生快捷键 + IPC 触发器（无需 `input` 组权限，响应 <15ms）
将以下快捷键绑定到桌面合成器配置即可：

**Hyprland (`~/.config/hypr/hyprland.conf`)**：
```ini
# 悬浮窗规则（确保浮空、置顶、无边框、不抢焦点）
windowrulev2 = float, class:^(riven-lens)$, title:^(RivenLens.*)$
windowrulev2 = pin, class:^(riven-lens)$, title:^(RivenLens.*)$
windowrulev2 = noborder, class:^(riven-lens)$, title:^(RivenLens.*)$
windowrulev2 = noshadow, class:^(riven-lens)$, title:^(RivenLens.*)$
windowrulev2 = nofocus, class:^(riven-lens)$, title:^(RivenLens.*)$

# 全局快捷键（支持改为你喜欢的按键）
bind = , F8, exec, /path/to/linux/run_wayland.sh --trigger toggle
bind = , F9, exec, /path/to/linux/run_wayland.sh --trigger wm
bind = CTRL ALT, D, exec, /path/to/linux/run_wayland.sh --trigger open_panel
bind = CTRL ALT, S, exec, /path/to/linux/run_wayland.sh --trigger export
bind = CTRL ALT, L, exec, /path/to/linux/run_wayland.sh --trigger lock
```

**Sway (`~/.config/sway/config`)**：
```ini
for_window [app_id="riven-lens" title="^RivenLens"] floating enable, sticky enable, border none
bindsym F8 exec /path/to/linux/run_wayland.sh --trigger toggle
bindsym F9 exec /path/to/linux/run_wayland.sh --trigger wm
bindsym Control+Mod1+d exec /path/to/linux/run_wayland.sh --trigger open_panel
```

**KDE Plasma / GNOME**：在系统「自定义快捷键」中分别绑定 `bash /path/to/linux/run_wayland.sh --trigger toggle`（`F8`）、`--trigger wm`（`F9`）、`--trigger open_panel`（`Ctrl+Alt+D`）。

---

## 四、Wayland 已知差异与限制说明

1. **游戏显示模式**：请将《星际战甲》（Steam Proton）设为**无边框全屏（Borderless Fullscreen）**或窗口化；独占全屏（Exclusive Fullscreen / 直接扫描输出）下悬浮窗无法覆盖游戏画面。
2. **悬浮窗自由定位（`move(x, y)`）**：纯原生 `xdg-shell` Wayland 协议禁止应用自行设定屏幕绝对坐标。`run_wayland.sh` 默认自动选用 **XWayland Qt 渲染 (`QT_QPA_PLATFORM=xcb`) + 原生 Wayland 截屏 (`grim`/`spectacle`)** 混合模式，从而完整保留自由拖拽、边缘磁吸对齐与精确开核桃对齐；若需纯原生 Wayland，可设 `RIVEN_LENS_DISPLAY=wayland`（需配合上述 Hyprland/Sway/KWin 窗口规则）。
3. **截屏速度与桌面差异**：
   - **Hyprland / Sway (`grim`)**：`< 30ms` 静默无弹窗，体验最佳。
   - **KDE Plasma (`spectacle -b -n -f`)**：静默后台截屏，约 `80~150ms`。
   - **GNOME Wayland**：受 Mutter 隐私策略限制，`gnome-screenshot` 可能会偶发闪屏或需要旧版权限；若出现截屏受限，建议在 GNOME 改用 X11 会话或切换 KDE/Hyprland。
4. **切出游戏自动隐藏（Smart Focus）**：
   - Hyprland（`hyprctl`）、Sway（`swaymsg`）、KDE（安装 `kdotool`）均完整支持；
   - 原生 GNOME Wayland 协议禁止读取其他窗口标题，程序会自动优雅降级为「保持悬浮窗可见」，不会误隐藏。
5. **SteamOS / Gamescope**：在 Gamescope 嵌套会话内运行时，建议切到桌面模式运行，或在嵌套外部捕获显示输出。
6. **跨平台打包**：Linux ELF 二进制需在 Linux 机器上运行 `bash linux/build_linux.sh` 构建（无法在 Windows 上直接生成 Linux 可执行文件）。
