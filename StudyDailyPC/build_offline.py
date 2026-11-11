# -*- coding: utf-8 -*-
"""组装电脑离线版：StudyDaily.exe + 全部视频，打成可直接分发的 zip"""
import os
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXE = HERE / "dist" / "StudyDaily.exe"
VIDEOS = ROOT / "StudyDaily" / "videos"
BOOKS = ROOT / "StudyDaily" / "workbooks"
OUT = ROOT / "StudyDaily-电脑离线版.zip"
TOP = "StudyDaily-电脑离线版"

README = """每日学习 · 电脑离线版
=====================================

怎么用
------
1. 把整个文件夹解压到硬盘上（建议放 D 盘，别放在压缩包里直接双击）
2. 双击 StudyDaily.exe，窗口几乎立刻出来（内置只留网页和语音，很小）

完全离线
--------
视频（videos 文件夹）、练习册（workbooks 文件夹）、语音都在这套文件里，
不需要联网，不会上传任何东西，也没有广告。

窗口怎么用
----------
· 窗口就是学习界面，没有地址栏，关掉窗口程序就退出了
· 学习进度自动保存，下次打开接着来
· 进度文件在：C:\\Users\\你的用户名\\AppData\\Local\\StudyDaily\\

文件说明
--------
StudyDaily.exe   主程序，双击运行
videos\\          124 节课的视频，一共约 3.9GB，别删
workbooks\\       练习册 619 页，删了就只能联网看
使用说明.txt      就是本文件

想搬到别的电脑
--------------
把整个文件夹拷贝过去就行（U 盘、移动硬盘都可以），不用安装。
"""


def main() -> None:
    if not EXE.is_file():
        raise SystemExit("找不到 %s，先运行 build.py" % EXE)
    if not VIDEOS.is_dir():
        raise SystemExit("找不到视频目录 %s" % VIDEOS)

    files = sorted(p for p in VIDEOS.rglob("*") if p.is_file())
    books = sorted(p for p in BOOKS.rglob("*") if p.is_file()) if BOOKS.is_dir() else []
    total = (sum(p.stat().st_size for p in files)
             + sum(p.stat().st_size for p in books) + EXE.stat().st_size)
    print("待打包 %d 个视频 + %d 页练习册，合计 %.2f GB"
          % (len(files), len(books), total / 1024 ** 3))

    # 视频已压缩过，用 STORED 直接搬运，速度快且压缩率无损失
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr(TOP + "/使用说明.txt", README.encode("utf-8-sig"))
        z.write(EXE, TOP + "/StudyDaily.exe")
        done = 0
        for p in files:
            rel = p.relative_to(VIDEOS).as_posix()
            z.write(p, TOP + "/videos/" + rel)
            done += 1
            if done % 20 == 0 or done == len(files):
                print("  已写入视频 %d/%d" % (done, len(files)), flush=True)
        for i, p in enumerate(books, 1):
            rel = p.relative_to(BOOKS).as_posix()
            z.write(p, TOP + "/workbooks/" + rel)
            if i % 100 == 0 or i == len(books):
                print("  已写入练习册 %d/%d" % (i, len(books)), flush=True)

    print("\n完成：%s  %.2f GB" % (OUT, OUT.stat().st_size / 1024 ** 3))


if __name__ == "__main__":
    main()
