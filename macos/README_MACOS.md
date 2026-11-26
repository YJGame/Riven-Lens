# 裂罅透镜 (Riven Lens) — macOS 适配版使用与编译指南

支持 **Apple Silicon (M1 / M2 / M3 / M4, `arm64`)** 与 **Intel (`x86_64`)**，最低要求 **macOS 12 Monterey** 及以上。  
所有核心功能与 Windows 版一致：单卡自动识别、洗卡双卡对比、聊天紫卡 F9 抓取、虚空遗物开核桃查价 + 入库标识、Warframe.market 实时查询、6 套主题 + 4 项自定义配色、15 种语言自动切换、内置《用户手册》及高清紫卡 PNG 导出。

---

## 一、一键编译 `.app` 与 `.dmg` 安装镜像（在 Mac 上运行）

> **前置要求**：Python 3.10 ~ 3.12（推荐通过 Homebrew 安装：`brew install python@3.12`）。

```bash
# 1. 解压源码包并进入目录
tar -xzf RivenLens_macOS_Source.tar.gz
cd RivenLens_macOS_Source

# 2. 一键创建虚拟环境、跑通全部单元测试、生成图标并编译 RivenLens.app 与 .dmg
bash macos/build_macos.sh
```

编译完成后将生成：
- **应用程序**：`dist_macos/RivenLens.app`（可直接拖进 `/Applications` 应用程序文件夹）
- **DMG 分发安装包**：`dist_macos/RivenLens_macOS_arm64.dmg`（或 `x86_64.dmg`，双击即可拖拽安装）

若不想打包，也可以在安装依赖后直接源码运行：
```bash
bash macos/run_macos.sh --source
```

---

## 二、首次运行必做：授予 2 项 macOS 隐私权限

由于 macOS 系统安全机制（TCC），请前往 **系统设置 → 隐私与安全性** 勾选允许 **RivenLens**（如果从终端源码运行，则勾选 **Terminal / iTerm2**）：

1. **屏幕录制（Screen Recording）**（必选）
   - 用于纯视觉截屏 OCR 识别游戏画面；不授予权限时只能截到桌面壁纸。
2. **辅助功能（Accessibility）** 或 **输入监控（Input Monitoring）**（推荐）
   - 用于在《星际战甲》（CrossOver / Whisky / GeForce NOW / Parallels）全屏前台时全局监听 `F8` / `F9` / `ESC` / `Ctrl+Alt+D` 等快捷键（完全非拦截模式，游戏内 `ESC` 正常响应）。
   - 如果暂时不想开全局键盘监控，也可通过 macOS「快捷指令 / Hammerspoon / Raycast」调用内置 IPC 指令触发：
     ```bash
     /Applications/RivenLens.app/Contents/MacOS/RivenLens --trigger toggle
     /Applications/RivenLens.app/Contents/MacOS/RivenLens --trigger wm
     /Applications/RivenLens.app/Contents/MacOS/RivenLens --trigger hide
     /Applications/RivenLens.app/Contents/MacOS/RivenLens --trigger open_panel
     ```

> 勾选权限后，请**彻底退出并重新打开一次 RivenLens** 使权限生效。

---

## 三、macOS 专属注意点与常见问题

1. **未签名应用 Gatekeeper 提示「无法打开 / 已损坏」**
   - 自己编译或未通过 Apple 开发者公证的 `.app` 在发给其他 Mac 时，macOS 可能会拦截。在终端执行一次移除隔离属性即可：
     ```bash
     xattr -cr /Applications/RivenLens.app
     ```
2. **Mac 键盘功能键 `F8` / `F9`**
   - Mac 笔记本默认将顶排键设为音量/亮度键，需要按 **`Fn + F8`** / **`Fn + F9`**，或者在 **系统设置 → 键盘 → 键盘快捷键 → 功能键** 中勾选「将 F1、F2 等键用作标准功能键」，也可以直接在裂罅透镜控制面板把热键改成自己顺手的组合键（支持 `Cmd` / `Option` / `Ctrl` / `Shift`）。
3. **《星际战甲》运行方式与显示模式**
   - 无论通过 **CrossOver / Whisky / Parallels Desktop / GeForce NOW / Moonlight** 游玩，建议将游戏设为 **无边框窗口 / 窗口化**，并首次使用「校准识别框」对齐卡片区域。
   - 程序已通过原生 Cocoa `NSWindow` 设置 `setHidesOnDeactivate(NO)`、`CanJoinAllSpaces` 与 `FullScreenAuxiliary`，切回游戏时悬浮窗不会消失且支持全屏空间悬浮置顶。
4. **macOS 15 Sequoia 截屏兼容**
   - 默认优先使用高速 `mss` 截屏；若 `mss` 在特定 macOS 15 环境下报错，会自动无缝回退到 macOS 原生无声 `screencapture -x -t png`。也可手动指定 `RIVEN_MAC_CAPTURE=screencapture`。
5. **配置文件与缓存路径**
   - 打包为 `.app` 运行时，所有个人设置、校准框坐标、价格缓存和下载的多语言 OCR 模型均保存在：
     `~/Library/Application Support/WarframeRivenOverlay/`
   - 不会污染只读 `.app` 包内部。
