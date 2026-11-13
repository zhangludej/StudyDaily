# -*- coding: utf-8 -*-
"""组装纯离线包：电脑版 / 手机版，各含主课 124 集 + 兴趣课 727 集 + 练习册。

兴趣课堂不做云端点播：视频全在本地 videos/i01~i16，
EXE 读旁边的 videos 文件夹，APK 用「家长端 → 设置 → 本地课件」选中文件夹。
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIDEOS = ROOT / "StudyDaily" / "videos"
BOOKS = ROOT / "StudyDaily" / "workbooks"
PDFS = ROOT / "StudyDaily" / "pdfs"


def readme(kind: str) -> str:
    common = """主课 124 集（拼音 / 识字 / 数学 / 英语）+ 兴趣课堂 727 集，共 851 集。
兴趣课堂在首页粉色的「兴趣课堂」按钮里：16 门（简笔画、绘画、涂色、情绪故事、
好习惯、垃圾分类、舞蹈、奥数启蒙、情商故事），孩子想看哪个点哪个，
随便看多少都不算每日任务，看完只记一个小勾。

完全离线
--------
所有视频都在 videos 文件夹里（约 15GB），不需要联网、不需要账号、不上传任何东西。
兴趣课堂不连网：如果某一集找不到，界面会提示你把 videos 文件夹放好，
不会卡在联网等待上。

PDF 课件
--------
首页 → 兴趣课堂，除了 16 门视频栏目，还有 3 个课件栏目：
· 识字字卡：40 集，田字格 + 大字 + 拼音，一页一字，可描红
· 拼音卡：四线三格，跟着课程走
· 课堂讲解页：124 集主课的重点板书/字卡画面（从视频里自动挑的静止画面）
课件一页一页翻，不进每日任务、不复习，看完只记一个小勾。
识字字卡和拼音卡每集还有一份 printable.pdf，拷出来就能打印。
以后想加课件：把新页放进 pdfs/ 并更新 pdfs/catalog.json，重启 App 就能看到，
不用重装、不用重新打包。
"""
    if kind == "pc":
        return """每日学习 · 电脑离线版
=====================================

怎么用
------
1. 把整个文件夹解压到硬盘上（建议放 D 盘，别在压缩包里直接双击）
2. 双击 StudyDaily.exe，窗口几乎立刻出来

""" + common + """
文件夹说明
----------
StudyDaily.exe   主程序，双击运行（约 41MB）
videos\\          851 集视频（u01~u04 主课 + i01~i16 兴趣课），别删
workbooks\\       练习册 619 页
pdfs\\           PDF 课件（字卡 / 拼音卡 / 讲解页 + 可打印 PDF）
使用说明.txt      就是本文件

窗口怎么用
----------
· 窗口就是学习界面，没有地址栏，关掉窗口程序就退出
· 学习进度自动保存，下次打开接着来
· 进度文件在：C:\\Users\\你的用户名\\AppData\\Local\\StudyDaily\\

想搬到别的电脑
--------------
把整个文件夹拷贝过去就行（U 盘、移动硬盘都可以），不用安装。
"""
    return """每日学习 · 手机离线版
=====================================

怎么用
------
1. 把整个文件夹解压，然后整个拷到手机存储里（用数据线或 U 盘）
2. 安装 StudyDaily.apk（系统提示「未知来源」时允许一次）
3. 打开 App，连点右上角齿轮 5 次输入 PIN（默认 1234）
4. 设置 → 本地课件 → 「选择离线课件文件夹」，选中能看到
   u01、i01 这些子文件夹的那一层（选它的上一层也行）
5. 返回孩子界面，就能全部离线看了

""" + common + """
文件夹说明
----------
StudyDaily.apk   安装包（约 41MB，证书和旧版一致，可直接覆盖安装，不丢进度）
videos\\          851 集视频（u01~u04 主课 + i01~i16 兴趣课），别删
workbooks\\       练习册 619 页
pdfs\\           PDF 课件（字卡 / 拼音卡 / 讲解页 + 可打印 PDF）
使用说明.txt      就是本文件

提示
----
· 手机上「兴趣课堂」和主课一样，都从你选中的文件夹里读视频
· 换手机时把整个文件夹拷过去，重新选一次文件夹即可，进度可用家长端导出备份
"""


def build(name: str, app_file: Path) -> Path:
    out = ROOT / (name + ".zip")
    top = name
    videos = sorted(p for p in VIDEOS.rglob("*") if p.is_file())
    books = sorted(p for p in BOOKS.rglob("*") if p.is_file()) if BOOKS.is_dir() else []
    pdfs = sorted(p for p in PDFS.rglob("*") if p.is_file() and "_tmp" not in p.parts) \
        if PDFS.is_dir() else []
    total = (sum(p.stat().st_size for p in videos)
             + sum(p.stat().st_size for p in books)
             + sum(p.stat().st_size for p in pdfs) + app_file.stat().st_size)
    print("[%s] %d 个视频 + %d 页练习册 + %d 个课件文件，合计 %.2f GB"
          % (name, len(videos), len(books), len(pdfs), total / 1024 ** 3), flush=True)

    tmp = out.with_suffix(".zip.tmp")
    # 视频已压缩过，用 STORED 直接搬运，速度快且无损
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr(top + "/使用说明.txt", readme("pc" if "电脑" in name else "phone").encode("utf-8-sig"))
        z.write(app_file, top + "/" + app_file.name)
        done = 0
        for p in videos:
            z.write(p, top + "/videos/" + p.relative_to(VIDEOS).as_posix())
            done += 1
            if done % 100 == 0 or done == len(videos):
                print("  视频 %d/%d" % (done, len(videos)), flush=True)
        for i, p in enumerate(books, 1):
            z.write(p, top + "/workbooks/" + p.relative_to(BOOKS).as_posix())
            if i % 200 == 0 or i == len(books):
                print("  练习册 %d/%d" % (i, len(books)), flush=True)
        for i, p in enumerate(pdfs, 1):
            z.write(p, top + "/pdfs/" + p.relative_to(PDFS).as_posix())
            if i % 500 == 0 or i == len(pdfs):
                print("  课件 %d/%d" % (i, len(pdfs)), flush=True)
    tmp.replace(out)  # 写完才替换，避免留下半截坏包
    print("[%s] 完成 %.2f GB -> %s" % (name, out.stat().st_size / 1024 ** 3, out), flush=True)
    return out


def main() -> None:
    exe = ROOT / "StudyDailyPC" / "dist" / "StudyDaily.exe"
    apk = ROOT / "StudyDaily.apk"
    for f in (exe, apk):
        if not f.is_file():
            raise SystemExit("找不到 %s" % f)
    if not VIDEOS.is_dir():
        raise SystemExit("找不到视频目录 %s" % VIDEOS)
    build("StudyDaily-电脑离线版", exe)
    build("StudyDaily-手机离线版", apk)


if __name__ == "__main__":
    main()
