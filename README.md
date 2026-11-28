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
- **自选武器词条 & 全武器灵化特化规则**：支持纯选择模式自定义单武器期望正负词条；覆盖全部官方灵化之源与扎里曼灵化武器，洗卡双卡对比内置钢铁之路实战 DPS 增幅估算，并支持生僻字武器名 OCR 纠偏。
- **交互式本地紫卡配装优化（内置 WFSim 分享链接跳转）**：
  - **毫秒级避稀释配卡**：围绕当前识别的紫卡自动推荐 7 张 Mod + 武器赋能，并支持用户随时手动替换任意槽位、第 9 格特殊功能（含近战 Tenno 气解卡）与赋能实时重算。
  - **悬停分类与复合元素搜索**：支持按伤害/多重、暴击/爆伤、元素伤害、物理伤害、派系歧视、武器专属等大类悬停选卡；输入 `腐蚀` / `病毒` / `辐射` / `磁力` 等复合元素可直接筛选对应基础元素卡。
  - **变种首选与效价加成**：同族武器自动首选高阶变种（如 `Prime` / `终幕` / `信条` / `赤毒`）并支持切换；赤毒 / 信条 / 终幕武器自动提供 **效价加成** 选择（默认 60% + 武器热门元素，可手动调节 25%~60% 与元素）并计入元素合成与伤害计算。
  - **按武器严格匹配流派与实时数值面板**：仅展示当前武器实际适用的热门流派（灵化 / 灵化非暴 / 触发异常 / 近战侵染 / 重击与重击震地等），实时展示千分位伤害、多重、射速/攻速、范围、双暴、触发及分元素数值。
  - **一键跳转 WFSim（wfsim.app）**：在配装优化器底部内置「在 WFSim 中打开这套配装（含第 9 格与效价加成）」与「仅导入紫卡到 WFSim」两种 v2 分享码跳转方式（非官方独立项目）。
- **切出游戏自动待命**：任何离开或切出游戏窗口（包括按 `Ctrl+Alt+D` 打开设置面板）时自动切回待命状态，零后台误识别。
- **自定义外观、字号自适应与磁吸贴边**：内置 6 套精选配色主题 + 4 项自定义颜色、10px~24px 全局字号自适应布局、窗口边缘磁吸贴边、无人值守自动折叠待命胶囊。
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
