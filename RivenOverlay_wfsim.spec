# -*- mode: python ; coding: utf-8 -*-
"""Dedicated PyInstaller specification for 裂罅透镜 · WFSim 联动版.

Builds an isolated standalone edition (`dist_wfsim/裂罅透镜_WFSim联动版/`) with
`RIVEN_WFSIM_EDITION=1` baked in via runtime hook, leaving standard `dist/裂罅透镜/`
completely untouched.

Build command:
    .venv_build\\Scripts\\pyinstaller.exe RivenOverlay_wfsim.spec --clean -y --distpath dist_wfsim
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
        "mss.windows",
        "keyboard",
        "pynput",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "PyQt6.sip",
        "shapely",
        "pyclipper",
        "yaml",
        "src.wfsim_bridge",
        "src.build_optimizer",
        "src.build_optimizer_dialog",
        "src.favorite_stats_dialog",
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
    runtime_hooks=[str(PROJECT_ROOT / "installer" / "wfsim_edition_rthook.py")],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

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
    exclude_binaries=True,
    name="裂罅透镜_WFSim联动版",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icon.ico"),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="裂罅透镜_WFSim联动版",
)
