# -*- mode: python ; coding: utf-8 -*-
"""Optimized PyInstaller specification for Warframe Riven Overlay.

Key slimming & anti-AV-false-positive settings:
  - Build inside clean `.venv_build` with `opencv-python-headless`.
  - `--onedir` (COLLECT folder mode, avoids onefile Temp DLL extraction alerts).
  - `uac_admin=False` (standard user launch without forced UAC shield).
  - `upx=False` (no UPX packing -> dramatically fewer heuristic AV alerts).
  - `console=False` (no black console window).
  - Excludes unused heavy Qt6/stdlib/scientific modules & unused video/OpenGL DLLs.

Build command:
    .venv_build\\Scripts\\pyinstaller.exe RivenOverlay.spec --clean -y
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

PROJECT_ROOT = Path(SPECPATH).resolve()

# 1. Collect RapidOCR models (.onnx), config.yaml, dictionaries (.txt), and ONNXRuntime libs
rapidocr_datas = collect_data_files("rapidocr_onnxruntime")
onnx_datas = collect_data_files("onnxruntime")
onnx_binaries = collect_dynamic_libs("onnxruntime")

# 2. Bundle project business assets: official weapon/attribute JSONs, i18n locales, and tray icons
project_datas = [
    (str(PROJECT_ROOT / "data"), "data"),
    (str(PROJECT_ROOT / "locales"), "locales"),
    (str(PROJECT_ROOT / "assets"), "assets"),
]

all_datas = rapidocr_datas + onnx_datas + project_datas
all_binaries = onnx_binaries

hidden_imports = (
    collect_submodules("rapidocr_onnxruntime")
    + [
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        "cv2",
        "mss",
        "mss.windows",
        "keyboard",
        "pynput",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "PyQt6.sip",
        "shapely",
        "pyclipper",
        "yaml",
    ]
)

excluded_modules = [
    # Heavy unused PyQt6 subsystems
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
    # Unused stdlib / scientific / test packages
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
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

# Trim unused bulky binaries (pure 2D QWidget + image OCR app never uses video FFmpeg, software OpenGL, or AVIF)
_DROP_BINARY_SUBSTRINGS = (
    "opencv_videoio_ffmpeg",
    "opengl32sw.dll",
    "d3dcompiler_47.dll",
    "qt6network.dll",
    "qt6qml",
    "qt6quick",
    "qt6sql",
    "qt6multimedia",
    "qt6webengine",
    "qt6opengl",
    "qt6pdf",
    "qt6printsupport",
    "qt6dbus",
    "_avif.",
)

a.binaries = [
    entry
    for entry in a.binaries
    if not any(s in entry[0].lower() or s in entry[1].lower() for s in _DROP_BINARY_SUBSTRINGS)
]

# Trim unused Qt6 translation catalogs (keep only zh_CN and en)
def _keep_data(entry) -> bool:
    dest_l = entry[0].replace("\\", "/").lower()
    if "pyqt6/qt6/translations/" in dest_l and dest_l.endswith(".qm"):
        fname = dest_l.rsplit("/", 1)[-1]
        return ("_zh_cn" in fname) or ("_en" in fname)
    return True

a.datas = [entry for entry in a.datas if _keep_data(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir folder mode (reduces AV false positives)
    name="裂罅透镜",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # Disable UPX packing to eliminate heuristic AV false positives
    console=False,          # --noconsole (hide black command prompt window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icon.ico"),
    uac_admin=False,        # Launch with standard user privileges (no forced UAC prompt)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="裂罅透镜",
)
