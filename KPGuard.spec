# PyInstaller build config. Run through build.ps1.
# Output: dist/KPGuard/KPGuard.exe (no console window, runs in the tray with --background)
from comtypes.client import GetModule
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# Generate UIA's type library on the build machine for the frozen Windows app.
GetModule("UIAutomationCore.dll")
a = Analysis(
    ["kp_guard.pyw"],
    datas=[("agent/assets", "agent/assets")],
    binaries=collect_dynamic_libs("uiautomation"),
    hiddenimports=["pystray._win32"] + collect_submodules("comtypes.gen"),  # pystray picks its backend at runtime
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
