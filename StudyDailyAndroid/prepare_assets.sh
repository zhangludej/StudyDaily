#!/usr/bin/env bash
# 把网页端资源同步进 APK 内置目录（assets/www）
# aapt2 无法处理中文文件名，因此「使用教程.html」在 App 内改名为 guide.html
set -euo pipefail

PROJ=$(cd "$(dirname "$0")" && pwd)
SRC=${SRC:-"$PROJ/../StudyDaily"}
DST="$PROJ/assets/www"

if [ ! -f "$SRC/index.html" ]; then
  echo "找不到网页端源码：$SRC（可用 SRC=... 指定）" >&2
  exit 1
fi

rm -rf "$DST"
mkdir -p "$DST/icons"

for f in index.html app.js styles.css planner.js courses.json quiz.json workbooks.json manifest.webmanifest; do
  cp "$SRC/$f" "$DST/"
done
cp -r "$SRC/assets" "$DST/assets"
mkdir -p "$DST/voice"
cp "$SRC"/voice/*.mp3 "$DST/voice/" 2>/dev/null || true
cp "$SRC"/voice/index.json "$DST/voice/" 2>/dev/null || true
cp "$SRC"/icons/*.png "$DST/icons/"
# 练习册 619 页 + 课程海报：内置后书页秒开，不依赖网络（59MB）
cp -r "$SRC/workbooks" "$DST/workbooks"
cp -r "$SRC/posters" "$DST/posters"
cp "$SRC/使用教程.html" "$DST/guide.html"

# 教程链接指向英文名
python - "$DST" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / 'index.html'
s = p.read_text(encoding='utf-8')
s = s.replace('使用教程.html', 'guide.html')
p.write_text(s, encoding='utf-8')
print('  已同步网页资源，教程入口改为 guide.html')
PY

cp "$SRC/icons/icon-192.png" "$PROJ/res/mipmap-xxhdpi/ic_launcher.png"

echo "assets 同步完成：$(find "$DST" -type f | wc -l) 个文件"
