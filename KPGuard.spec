# PyInstaller build config. Run through build.ps1.
# Output: dist/KPGuard/KPGuard.exe (no console window, runs in the tray with --background)
a = Analysis(
    ["kp_guard.pyw"],
    datas=[("agent/assets", "agent/assets")],
    hiddenimports=["pystray._win32"],  # pystray picks its backend at runtime
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KPGuard",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="KPGuard")
