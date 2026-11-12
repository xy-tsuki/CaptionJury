# PyInstaller 打包配置：输出 dist\CaptionJury\CaptionJury.exe（文件夹模式，启动快，安装包再把整个文件夹装进去）。
#     python -m PyInstaller packaging/captionjury.spec --noconfirm
# 在仓库根目录运行。
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "ui"), "ui")],
    excludes=["tkinter", "unittest", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CaptionJury",
    icon=str(ROOT / "packaging" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CaptionJury", upx=False)
