# Build on macOS: python -m PyInstaller --noconfirm KPGuard-macos.spec
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules('pystray') + ['AppKit', 'Foundation', 'Quartz']
a = Analysis(['kp_guard.pyw'], datas=[('agent/assets', 'agent/assets')], hiddenimports=hidden)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KPGuard', console=False)
coll = COLLECT(exe, a.binaries, a.datas, name='KPGuard')
app = BUNDLE(coll, name='KPGuard.app', bundle_identifier='com.ibalance.kpguard',
             info_plist={'NSHighResolutionCapable': True})
