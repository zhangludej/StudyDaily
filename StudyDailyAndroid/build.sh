#!/usr/bin/env bash
# 每日学习 APK 构建脚本（不依赖 Android Studio / Gradle）
# 依赖：JDK 17 + Android SDK build-tools 34 + platform android-34
# 用法：bash prepare_assets.sh && bash build.sh
set -euo pipefail

PROJ=$(cd "$(dirname "$0")" && pwd)
SDK=${SDK_POSIX:-/c/sdbuild/sdk}
JDK=${JDK_POSIX:-/c/sdbuild/jdk17}
BT="$SDK/build-tools/34.0.0"
AJAR="$SDK/platforms/android-34/android.jar"

if [ ! -f "$AJAR" ]; then
  echo "找不到 $AJAR，请先安装 platforms;android-34" >&2
  exit 1
fi
if [ ! -x "$JDK/bin/java" ] && [ ! -f "$JDK/bin/java.exe" ]; then
  echo "找不到 JDK：$JDK" >&2
  exit 1
fi

export JAVA_HOME="$(cygpath -w "$JDK")"
export PATH="$JDK/bin:$PATH"

# Windows 版工具需要 Windows 形式路径
W_PROJ=$(cygpath -w "$PROJ")
W_AJAR=$(cygpath -w "$AJAR")

OUT="$PROJ/build"
W_OUT=$(cygpath -w "$OUT")
rm -rf "$OUT"
mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex"

echo "[1/6] 编译资源 (aapt2 compile)"
"$BT/aapt2.exe" compile --dir "$W_PROJ\\res" -o "$W_OUT\\res.zip"

echo "[2/6] 链接资源与清单 (aapt2 link)"
"$BT/aapt2.exe" link \
  -o "$W_OUT\\base.apk" \
  -I "$W_AJAR" \
  --manifest "$W_PROJ\\AndroidManifest.xml" \
  -A "$W_PROJ\\assets" \
  --java "$W_OUT\\gen" \
  --min-sdk-version 24 \
  --target-sdk-version 34 \
  --auto-add-overlay \
  "$W_OUT\\res.zip"

echo "[3/6] 编译 Java (javac)"
# 用相对路径（在工程根目录下）调用，避免 Windows javac 不识别 /c/... 路径
( cd "$PROJ" && find src build/gen -name '*.java' | sort > build/sources.txt )
if ! ( cd "$PROJ" && javac -encoding UTF-8 -nowarn -source 1.8 -target 1.8 \
        -bootclasspath "$W_AJAR" -d "$W_OUT\\classes" @"$W_OUT\\sources.txt" ); then
  echo "  -bootclasspath 方式不可用，改用 -classpath 重试"
  ( cd "$PROJ" && javac -encoding UTF-8 -nowarn -source 1.8 -target 1.8 \
      -classpath "$W_AJAR" -d "$W_OUT\\classes" @"$W_OUT\\sources.txt" )
fi
echo "  class 文件：$(find "$OUT/classes" -name '*.class' | wc -l) 个"

echo "[4/6] 转 DEX (d8)"
( cd "$PROJ" && find build/classes -name '*.class' | sed 's#/#\\#g' | sort > build/classlist.txt )
( cd "$PROJ" && "$BT/d8.bat" --lib "$W_AJAR" --min-api 24 --output "$W_OUT\\dex" @"$W_OUT\\classlist.txt" > /dev/null )
if [ ! -f "$OUT/dex/classes.dex" ]; then
  echo "d8 未生成 classes.dex" >&2
  exit 1
fi

echo "[5/6] 打包 APK"
python - "$OUT" <<'PY'
import shutil, sys, zipfile
from pathlib import Path
out = Path(sys.argv[1])
shutil.copyfile(out / 'base.apk', out / 'unsigned.apk')
with zipfile.ZipFile(out / 'unsigned.apk', 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(out / 'dex' / 'classes.dex', 'classes.dex')
print('  已写入 classes.dex')
PY

echo "[6/6] 对齐与签名"
"$BT/zipalign.exe" -f -p 4 "$W_OUT\\unsigned.apk" "$W_OUT\\aligned.apk"

KS="$PROJ/keystore/studydaily.jks"
if [ ! -f "$KS" ]; then
  mkdir -p "$PROJ/keystore"
  keytool -genkeypair -v -keystore "$(cygpath -w "$KS")" -alias studydaily \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass studydaily -keypass studydaily \
    -dname "CN=StudyDaily, OU=Home, O=StudyDaily, L=Beijing, C=CN" > /dev/null 2>&1
  echo "  已生成签名证书"
fi

"$BT/apksigner.bat" sign \
  --ks "$(cygpath -w "$KS")" --ks-key-alias studydaily \
  --ks-pass pass:studydaily --key-pass pass:studydaily \
  --min-sdk-version 24 \
  --out "$W_OUT\\StudyDaily.apk" "$W_OUT\\aligned.apk"

"$BT/apksigner.bat" verify --print-certs "$W_OUT\\StudyDaily.apk" | head -n 3

echo ""
echo "构建完成：$OUT/StudyDaily.apk  ($(du -h "$OUT/StudyDaily.apk" | cut -f1))"
