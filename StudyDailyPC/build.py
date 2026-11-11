# -*- coding: utf-8 -*-
"""把桌面版打成单个 EXE：pyinstaller onefile + web 资源内置"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON = HERE / "app.ico"


def make_icon() -> bool:
    if ICON.is_file():
        return True
    src = HERE / "web" / "icons" / "icon-512.png"
    if not src.is_file():
        return False
    try:
        from PIL import Image
        img = Image.open(src)
        img.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                              (128, 128), (256, 256)])
        return True
    except Exception as e:
        print("图标生成失败：", e)
        return False


def main() -> None:
    has_icon = make_icon()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "StudyDaily",
        "--add-data", str(HERE / "web") + ";web",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build-pyi"),
        "--specpath", str(HERE / "build-pyi"),
    ]
    if has_icon:
        cmd += ["--icon", str(ICON)]
    cmd.append(str(HERE / "server.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    out = HERE / "dist" / "StudyDaily.exe"
    if out.is_file():
        print("\n完成：", out, "  %.1f MB" % (out.stat().st_size / 1048576))
    else:
        raise SystemExit("打包失败，未生成 exe")


if __name__ == "__main__":
    main()
