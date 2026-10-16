# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller para gerar o executável do DriveGuard AI (Windows).

Uso (com o ambiente virtual ativado):
    pip install -r requirements-dev.txt
    python scripts/make_icon.py
    pyinstaller driveguard.spec --noconfirm

Saída: dist/DriveGuardAI/DriveGuardAI.exe (modo pasta, inicia mais rápido que onefile).
"""

from PyInstaller.utils.hooks import collect_dynamic_libs

# O MediaPipe 1.x é uma DLL nativa carregada via ctypes; o PyInstaller não a
# detecta pela análise de imports, por isso é coletada explicitamente.
binaries = collect_dynamic_libs("mediapipe")

datas = [
    ("assets/icons", "assets/icons"),
    ("assets/models", "assets/models"),
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=["mediapipe.tasks.python.vision"],
    # matplotlib e PIL NÃO podem ser excluídos: o MediaPipe os importa ao carregar.
    excludes=["tkinter", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DriveGuardAI",
    console=False,
    icon="assets/icons/driveguard.ico",
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="DriveGuardAI", upx=False)
