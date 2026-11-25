# -*- mode: python ; coding: utf-8 -*-
"""Linux (Wayland & X11) PyInstaller specification for 裂罅透镜 (Riven Lens).

Run on Linux:
    bash linux/build_linux.sh
or:
    pyinstaller RivenOverlay_linux.spec --clean -y --distpath dist_linux
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
        "mss.linux",
        "evdev",
        "evdev.ecodes",
        "pynput",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "PyQt6.sip",
        "shapely",
        "pyclipper",
        "yaml",
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
    name="riven-lens",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="riven-lens",
)
