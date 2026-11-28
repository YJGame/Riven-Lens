# 裂罅透镜 (Riven Lens)

**裂罅透镜 (Riven Lens)** 是一款面向《星际战甲》（Warframe）的**纯视觉 OCR 紫卡估价与虚空遗物（开核桃）实时悬浮窗工具**。  
全程仅基于屏幕画面识别（**零内存读取、零游戏注入**），支持 **Windows**、**macOS (Apple Silicon & Intel)**(未测试) 与 **Linux Wayland**(未测试)。

本程序使用antigravity,全AI生成! 
---

## 下载安装（Windows）

前往本仓库右侧的 **[Releases](../../releases/latest)** 页面下载：

- **推荐**：`裂罅透镜_Windows_安装包.exe`（图形化自解压安装包，双击选择安装目录即可）
- **便携版**：`裂罅透镜_Windows_绿色版.zip`（免安装，解压即用）

> 首次启动将自动弹出内置《用户手册》，之后随时可按 `Ctrl+Alt+D` 打开设置面板点击「用户手册」回看。  
> 若游戏或 WeGame 以管理员权限运行，请使用目录内的 `启动悬浮窗_管理员.bat` 启动。

---

## 核心功能

- **多模式识别**：
  - **单卡自动识别**：武器页/配卡界面实时识别紫卡词条、定位评分与热门词条命中。
  - **洗卡双卡对比**：赤毒洗卡界面左右双卡并排打分对比，快速判断保留左侧还是右侧。
  - **聊天紫卡抓取（F9）**：在交易/聊天频道点开紫卡链接后按 `F9` 一键识别并估价。
  - **虚空遗物开核桃（F9）**：4 卡奖励界面一键查询 `warframe.market` 均价、杜卡德金币与入库（Vaulted）状态。
- **Warframe.market 实时查价**：支持一键查询同武器紫卡市场挂单参考价，以及 Prime 部件与核桃价格同步。
- **自选武器词条 & 41 把灵化特化规则**：支持纯选择模式自定义单武器期望正负词条；洗卡双卡对比内置钢铁之路实战 DPS 增幅估算。
- **本地紫卡配装优化 & 兼容 WFSim 分享链接**：内置毫秒级本地 7 Mod + 赋能自动避稀释配卡推荐，并支持在设置面板一键「在 WFSim 中打开（wfsim.app）」（默认仅导入当前紫卡数值，由站内模拟与优化）。
- **切出游戏自动待命**：任何离开或切出游戏窗口（包括按 `Ctrl+Alt+D` 打开设置面板）时自动切回待命状态，零后台误识别。
- **自定义外观与磁吸贴边**：内置 6 套精选配色主题 + 4 项自定义颜色、窗口边缘磁吸贴边、无人值守自动折叠待命胶囊。
- **15 种界面与 OCR 语言**：自动按语言加载对应的 RapidOCR ONNX 识别模型。
- **精美卡片导出**：`Ctrl+Alt+S` 一键生成高清紫卡展示图并复制到剪贴板。

---

## 默认快捷键（可在设置面板自定义）

| 快捷键 | 功能说明 |
| --- | --- |
| `F8` | 轮询切换模式（单卡自动 → 洗卡双卡 → 聊天 F9 → 开核桃 F9 → 暂停） |
| `F9` | 单卡/双卡下查询 Warframe.market；聊天/开核桃下触发识别 |
| `ESC` | 收起悬浮窗为待命胶囊（保留当前模式） |
| `Ctrl+Alt+D` | 打开 / 隐藏设置与控制面板 |
| `Ctrl+Alt+S` | 导出紫卡展示图 PNG 并复制到剪贴板 |
| `Ctrl+Alt+L` | 锁定 / 解锁防误触热键 |

---

## 从源码运行与打包

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

打包 Windows 可执行文件与分发安装包：

```powershell
# 1. PyInstaller 编译到 dist\裂罅透镜
.\.venv_build\Scripts\pyinstaller.exe RivenOverlay.spec --clean -y

# 2. 生成自解压安装包 (.exe) 与绿色压缩包 (.zip) 到 release\
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_windows_release.ps1
```

### macOS (Apple Silicon & Intel)

详见 [macos/README_MACOS.md](macos/README_MACOS.md)，一键编译 `.app` 与 `.dmg` 安装镜像：

```bash
bash macos/build_macos.sh
```

### Linux (Wayland)

详见仓库内 [linux/](linux/) 目录与打包脚本，支持 KDE Plasma / GNOME / Hyprland / Sway Wayland 环境编译与运行：

```bash
bash linux/build_linux.sh
```

---

## 声明

本工具仅通过操作系统公开截屏接口进行图像文字识别（OCR），不读取游戏进程内存、不修改游戏文件、不执行任何自动操作。游戏数据与市场价格来自 `warframe.market` 与 `WFCD/warframe-items`。  
本工具兼容 [WFSim (wfsim.app)](https://wfsim.app) 分享链接（`?b=`）跳转，仅在用户手动点击面板按钮时打开本地浏览器；**裂罅透镜 (Riven Lens)** 为非官方独立开源项目，与 WFSim 及其作者无官方隶属或背书关系。
