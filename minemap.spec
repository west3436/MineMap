# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: onedir console build named MineMap.
# Build with:  pyinstaller minemap.spec
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    ("minemap/web", "minemap/web"),
    ("minemap/pipeline/templates", "minemap/pipeline/templates"),
    ("minemap/data/kg_classes.json", "minemap/data"),
]

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "tifffile",
    "shapefile",
    "nbtlib",
    "tkinter",
    "tkinter.filedialog",
] + collect_submodules("minemap")

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "notebook", "imagecodecs"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MineMap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MineMap",
)
