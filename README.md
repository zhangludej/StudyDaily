# StudyDaily 每日学习 · 幼小衔接离线学习 App

给 6 岁不识字的小朋友做的**完全离线**安卓学习 App：视频课件、语音引导、插画、
学后小游戏出题、翻页练习册书架，家长端可以管时长、看统计、设 PIN。
全部内容本地运行，不需要联网、没有任何广告和内购。

## 功能一览

- **每日自动排课**：按周课表排科目——周一/周三「识字＋数学」、周二「识字＋拼音」、
  周四「识字＋英语」、周五「识字＋数学/拼音」隔周轮换（家长端可固定）、
  周六自由拓展、周日只复习。搭配日强制两科各至少一节，科目内不跳课、整节不拆、
  顺延不加量，1/3/7/15 天间隔复习。
- **学后小游戏出题**：每节课后自动出 3 道图文题（124 节课 372 题），三颗星＋小熊
  走路＋连击星星，答错两次自动点亮答案，不挫败。
- **练习册书架**：8 本幼小衔接教材共 649 页，翻页阅读、自动记住看到哪页。
- **语音引导**：403 句 TTS 语音（中文女声＋英文儿声），全部离线打包。
- **家长端**：每日预算、复习上限、出题开关、周五搭配、PIN 码、学习统计。

## 视频课件：云端点播，装完就能用

APK 只有 14 MB，**不含视频**。首次打开某节课时，App 内置的本地 HTTP 服务会向云端源
发起带 `Range` 的请求，边下边播（720p 约 100 KB/s 即可），同时后台把整集存进应用私有缓存：

- 看过的课 → 之后**离线可用**，秒开
- 缓存上限 2 GB，超了自动淘汰最久没看的；家长端可查看/清空
- 多源自动测速：`gh-proxy.com` → `ghfast.top` → GitHub raw → `ghproxy.net`
- 本地 `/内部存储/StudyDaily/videos/` 有文件时优先读本地（完全离线）

课件源：https://github.com/zhangludej/StudyDaily-courseware

所以对方只需要：**装 APK → 打开 → 点课**，不需要拷文件夹、不需要给存储权限。

## 目录结构

```
StudyDaily/            网页端源码（也是安卓 App 的 assets/www 来源）
├── index.html         单页应用入口
├── app.js             全部界面与交互逻辑
├── planner.js         排课引擎（周课表、顺延、间隔复习）
├── courses.json       课程目录（124 节课）
├── quiz.json          372 道小游戏题目
├── workbooks.json     练习册书目
├── 使用教程.html       给家长的图文教程
├── assets/            插画（Fluent Emoji 3D）
├── voice/             403 句离线语音
├── workbooks/         练习册 649 页（翻页书架用）
├── videos/            视频课件（不随仓库分发，见下文）
└── tools/             构建脚本与测试
    ├── build_quiz.py      按课生成 quiz.json
    ├── build_workbooks.py PDF 转 649 页练习册
    ├── make_voice.py      TTS 语音批量生成
    ├── fetch_pics.py      抓取 Fluent Emoji 插画
    └── test-planner.js    排课引擎 25 项单元测试

StudyDailyAndroid/     安卓 WebView 壳工程
├── AndroidManifest.xml
├── build.sh           一键构建签名 APK
└── prepare_assets.sh  把 StudyDaily 同步进 assets/www

StudyDaily.apk         成品安装包（约 14 MB，不含视频课件）
```

## 本地运行

```bash
cd StudyDaily
python -m http.server 8000
# 浏览器打开 http://127.0.0.1:8000
```

## 构建 APK

需要 JDK + Android SDK（aapt2 / d8 / apksigner）。**注意：必须在纯英文路径下构建**，
中文路径会导致 aapt2 找不到文件。

```bash
SRC=/path/to/StudyDaily bash prepare_assets.sh
bash build.sh
```

签名证书不会随仓库分发（公开仓库泄露签名密钥会被用于伪造更新包）。
`build.sh` 在 `keystore/studydaily.jks` 缺失时会自动生成一个新证书；
要和已有安装包兼容升级，请自行放入原证书。

## 视频课件

`StudyDaily/videos/` 存放各单元视频课件（约 3.9 GB），体积超出 GitHub 仓库限制，
不随仓库分发。目录按 `courses.json` 里每节课的 `file` 字段命名，
App 检测到视频缺失时会降级为图文页面。

## 测试

```bash
cd StudyDaily
node tools/test-planner.js   # 排课引擎 25 项单元测试
```

## 许可

个人自用项目，教材内容版权归原出版社，请勿商用。
