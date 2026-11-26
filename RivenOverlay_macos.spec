# -*- mode: python ; coding: utf-8 -*-
"""macOS (Apple Silicon & Intel) PyInstaller `.app` bundle specification for 裂罅透镜 (Riven Lens).

Run on macOS:
    bash macos/build_macos.sh
or:
    pyinstaller RivenOverlay_macos.spec --clean -y --distpath dist_macos
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

PROJECT_ROOT = Path(SPECPATH).resolve()

rapidocr_datas = collect_data_files("rapidocr_onnxruntime")
onnx_datas = collect_data_files("onnxruntime")
onnx_binaries = collect_dynamic_libs("onnxruntime")

project_datas = [
    (str(PROJECT_ROOT / "data"), "data"),
    (str(PROJECT_ROOT / "locales"), "locales"),
    (str(PROJECT_ROOT / "assets"), "assets"),
]

hidden_imports = (
    collect_submodules("rapidocr_onnxruntime")
    + [
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        "cv2",
        "mss",
        "mss.darwin",
        "pynput",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "PyQt6.sip",
        "shapely",
        "pyclipper",
        "yaml",
        "src.macos_support",
        "src.wayland_support",
        "src.user_manual_dialog",
    ]
)

excluded_modules = [
    "PyQt6.QtWebEngine",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DRender",
    "PyQt6.Qt3DInput",
    "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DExtras",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtNetwork",
    "PyQt6.QtOpenGL",
    "PyQt6.QtOpenGLWidgets",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtDBus",
    "PyQt6.QtPdf",
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
    "tkinter",
    "unittest",
    "pydoc",
    "matplotlib",
    "scipy",
    "pandas",
    "pytest",
    "IPython",
    "jupyter",
]

icns_path = PROJECT_ROOT / "assets" / "icon.icns"
icon_arg = str(icns_path) if icns_path.is_file() else None

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=onnx_binaries,
    datas=rapidocr_datas + onnx_datas + project_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RivenLens",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RivenLens",
)

app = BUNDLE(
    coll,
    name="RivenLens.app",
    icon=icon_arg,
    bundle_identifier="com.rivenlens.warframe.overlay",
    info_plist={
        "CFBundleName": "裂罅透镜",
        "CFBundleDisplayName": "裂罅透镜 (Riven Lens)",
        "CFBundleShortVersionString": "3.0.0",
        "CFBundleVersion": "3.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSScreenCaptureUsageDescription": "裂罅透镜需要屏幕录制权限以进行纯视觉 OCR 紫卡与遗物识别（不读取游戏内存）。",
        "NSAppleEventsUsageDescription": "裂罅透镜用于检测当前前台应用以在切出《星际战甲》时自动隐藏悬浮窗。",
    },
)
