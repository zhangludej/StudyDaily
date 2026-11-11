# -*- coding: utf-8 -*-
"""把桌面版打成单个 EXE：pyinstaller onefile + web 资源内置"""
import shutil
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


def stage_web() -> Path:
    """只把网页代码打进 EXE；练习册 619 页和海报太大，外置（本机文件夹或云端按需取）。

    onefile 每次启动都要把内置资源解压到临时目录，内置 76MB 就得等 3~5 秒；
    去掉这两块后 EXE 只剩十几 MB，窗口几乎是立刻出来的。
    """
    src = HERE / "web"
    dst = HERE / "build-pyi" / "web"
    if dst.exists():
        shutil.rmtree(dst)
    skip = {"workbooks"}   # 海报只在本地有，1.5MB，留着内置
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in skip:
            continue
        if item.is_dir():
            shutil.copytree(item, dst / item.name)
        else:
            shutil.copy2(item, dst / item.name)
    total = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file())
    print("内置网页资源：%.1f MB（已排除练习册 619 页）" % (total / 1048576))
    return dst


def main() -> None:
    has_icon = make_icon()
    web = stage_web()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "StudyDaily",
        "--add-data", str(web) + ";web",
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
