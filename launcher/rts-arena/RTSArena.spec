# PyInstaller 빌드 설정 — build.bat 으로 실행
# 결과: dist\RTS-Arena.exe (단일 파일, 관리자 권한 자동 요청)
a = Analysis(['arena.py'], pathex=[], binaries=[],
             datas=[('assets', 'assets')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PIL', 'numpy'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='RTS-Arena',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False,
          icon='assets/app.ico', uac_admin=True)
