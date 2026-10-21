#!/usr/bin/env python3
"""
Nabu 卡线一体包构建工具
结合线刷包的完整分区与卡刷模板的打包方式，生成 zip 刷机包。

用法:
    python3 build.py                         # 使用默认配置
    python3 build.py --line-zip ./line.zip   # 指定线刷包路径
    python3 build.py --dry-run               # 仅显示规划
"""

import argparse
import datetime
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 用户配置区 — 按需修改
# ═══════════════════════════════════════════════════════════════

PACKAGE_NAME   = "LineageOS 23.2"       # 包名
DEVICE_NAME    = "Xiaomi Pad 5 (nabu)"  # 机型
ANDROID_VER    = "Android 13"           # Android 版本
BUNDLE_NUMBER  = ""                     # 构建号（可选）
PATCH_LEVEL    = ""                     # 安全补丁级别（可选）
BUILD_DATE     = ""                     # 构建日期（可选）
AUTHOR         = ""                     # 作者

LINE_DOWNLOAD  = ""   # 线刷包下载链接（留空则用 LINE_LOCAL）
CARD_DOWNLOAD  = ""   # 卡刷模板下载链接（留空则用 CARD_LOCAL）
LINE_LOCAL     = "lineflash/"   # 本地线刷包目录
CARD_LOCAL     = "card_template/"  # 本地卡刷模板目录
OUTPUT_DIR     = "build_output/"
OUTPUT_NAME    = ""             # 留空则自动生成

COMPRESSION_RATIO = 0.60        # 估算压缩率（压缩后占原始大小比例）

# ═══════════════════════════════════════════════════════════════
# 分区定义
# ═══════════════════════════════════════════════════════════════

SLOTTED_PARTS = {"vbmeta", "vbmeta_system", "dtbo", "vendor_boot", "boot", "keymaster"}

NON_SLOTTED_PARTS = {
    "super", "userdata", "metadata",
    # QCOM firmware
    "xbl", "xbl_config", "abl", "tz", "hyp", "devcfg", "storsec",
    "cmnlib", "cmnlib64", "aop", "qupv3fw", "imagefv",
    "uefi_sec", "multi_image", "km41",
    # Others
    "modem", "dsp", "bluetooth", "cust", "persist", "rescue", "misc",
}

ALL_KNOWN_PARTS = SLOTTED_PARTS | NON_SLOTTED_PARTS

# 线刷包文件名 -> 分区名 映射
FILE_TO_PART = {
    "xbl.elf":         "xbl",
    "xbl_config.elf":  "xbl_config",
    "abl.elf":         "abl",
    "tz.mbn":          "tz",
    "hyp.mbn":         "hyp",
    "devcfg.mbn":      "devcfg",
    "storsec.mbn":     "storsec",
    "cmnlib.mbn":      "cmnlib",
    "cmnlib64.mbn":    "cmnlib64",
    "aop.mbn":         "aop",
    "qupv3fw.elf":     "qupv3fw",
    "imagefv.elf":     "imagefv",
    "uefi_sec.mbn":    "uefi_sec",
    "multi_image.mbn": "multi_image",
    "km41.mbn":        "km41",
    "NON-HLOS.bin":    "modem",
    "dspso.bin":       "dsp",
    "BTFM.bin":        "bluetooth",
    "boot.img":        "boot",
    "dtbo.img":        "dtbo",
    "vendor_boot.img": "vendor_boot",
    "vbmeta.img":      "vbmeta",
    "vbmeta_system.img": "vbmeta_system",
    "keymaster.img":   "keymaster",
    "super.img":       "super",
    "userdata.img":    "userdata",
    "metadata.img":    "metadata",
    "cust.img":        "cust",
    "persist.img":     "persist",
    "rescue.img":      "rescue",
    "misc.img":        "misc",
}


def log(msg):
    print(f"[INFO] {msg}")


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def crc32_file(path: str) -> int:
    h = zlib.crc32(b"")
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h = zlib.crc32(chunk, h)
    return h & 0xFFFFFFFF


def download_file(url: str, dest: str):
    log(f"Downloading {dest} ...")
    import urllib.request
    urllib.request.urlretrieve(url, dest)
    log(f"OK {dest} ({os.path.getsize(dest):,} bytes)")


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 解析线刷包
# ═══════════════════════════════════════════════════════════════

def extract_lineflash(lineflash_source: str, out_dir: str, use_directory: bool = False) -> dict:
    """
    Extract line flash package, rename images to partition names.
    use_directory=True reads from directory directly (no zipfile).
    Returns {part: path}
    """
    log(f"Parsing line flash: {lineflash_source}")
    images = {}

    def add_image(src_path: str, basename: str):
        part = FILE_TO_PART.get(basename)
        if not part:
            stem = os.path.splitext(basename)[0]
            if stem in FILE_TO_PART.values():
                part = stem
            else:
                return
        dest = os.path.join(out_dir, f"{part}.img")
        log(f"  {basename:30s} -> {part}.img")
        shutil.copy2(src_path, dest)
        images[part] = dest

    if use_directory:
        # Directory mode: scan images/ subdirectory or root
        img_subdir = os.path.join(lineflash_source, "images")
        if not os.path.isdir(img_subdir):
            img_subdir = lineflash_source
        for basename in os.listdir(img_subdir):
            src = os.path.join(img_subdir, basename)
            if os.path.isfile(src):
                add_image(src, basename)
        # Also scan root for non-script files
        skip_names = {"flash_all.sh", "flash_all.bat",
                      "flash_all_except_storage.sh", "flash_all_except_storage.bat"}
        for basename in os.listdir(lineflash_source):
            if basename in skip_names:
                continue
            src = os.path.join(lineflash_source, basename)
            if os.path.isfile(src):
                add_image(src, basename)
    else:
        # ZIP mode
        with zipfile.ZipFile(lineflash_source, "r") as z:
            for name in z.namelist():
                basename = os.path.basename(name)
                if not basename:
                    continue
                part = FILE_TO_PART.get(basename)
                if not part:
                    stem = os.path.splitext(basename)[0]
                    if stem in FILE_TO_PART.values():
                        part = stem
                    else:
                        continue
                dest = os.path.join(out_dir, f"{part}.img")
                log(f"  {basename:30s} -> {part}.img")
                with z.open(name) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                images[part] = dest
    return images


# ═══════════════════════════════════════════════════════════════
# 压缩镜像
# ═══════════════════════════════════════════════════════════════

def compress_images(images: dict, img_dir: str) -> list:
    """Compress .img -> .img.zst, returns [(part, zst_path, comp_size, crc, raw_size)]"""
    results = []
    for part_name in sorted(images.keys()):
        img_path = images[part_name]
        zst_path = os.path.join(img_dir, f"{part_name}.img.zst")
        raw_size = os.path.getsize(img_path)
        if raw_size == 0:
            open(zst_path, "wb").close()
            results.append((part_name, zst_path, 0, 0, 0))
            log(f"  {part_name}: empty image, skipping compression")
            continue
        log(f"  {part_name}: {raw_size:,} bytes -> compressing ...")
        try:
            subprocess.run(
                ["zstd", "-q", "--long=27", "-f", "-o", zst_path, img_path],
                check=True, capture_output=True,
            )
        except FileNotFoundError:
            die("zstd not found, please install zstd")
        comp_size = os.path.getsize(zst_path)
        crc = crc32_file(zst_path)
        results.append((part_name, zst_path, comp_size, crc, raw_size))
    return results


# ═══════════════════════════════════════════════════════════════
# 计算 ZIP 内部偏移
# ═══════════════════════════════════════════════════════════════

def calc_zip_offsets(file_list: list) -> dict:
    """
    Simulate ZIP writing to compute internal offsets.
    file_list: [(arcname, data_bytes, compress)]
    Returns {arcname: (data_start, comp_size, file_size)}
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data, do_compress in file_list:
            if do_compress and len(data) > 0:
                zf.writestr(arcname, data)
            else:
                zi = zipfile.ZipInfo(arcname)
                zf.writestr(zi, b"")
    buf.seek(0)
    offsets = {}
    with zipfile.ZipFile(buf, "r") as zf:
        for info in zf.infolist():
            fn = info.filename.encode()
            data_start = info.header_offset + 30 + len(fn) + len(info.extra)
            offsets[info.filename] = (data_start, info.compress_size, info.file_size)
    return offsets


# ═══════════════════════════════════════════════════════════════
# 生成 update-binary
# ═══════════════════════════════════════════════════════════════

def build_update_binary(template_path: str, layout_entries: list, package_info: dict) -> bytes:
    """Generate update-binary from template, replacing LAYOUT and package info"""
    with open(template_path, "rb") as f:
        content = f.read()

    # Replace LAYOUT
    layout_str = "\n".join(
        f"{name} {offset} {size} {crc} {raw_size}"
        for name, offset, size, crc, raw_size in layout_entries
    )
    layout_replacement = ("LAYOUT=\"" + layout_str + "\"").encode()
    content = re.sub(rb'LAYOUT="[^"]*"', layout_replacement, content, count=1)

    # Replace package info block
    # Find the FIRST ui_print block that contains the separator lines (package info header)
    # This is after the function definitions, before slot_ok=0
    sep = "================================================"
    sep_bytes = sep.encode()

    # Find slot_ok=0
    slot_check = content.find(b"slot_ok=0")
    if slot_check < 0:
        die("Cannot find slot_ok=0 in update-binary template")

    # Find the LAST separator line before slot_ok
    last_sep = content.rfind(sep_bytes, 0, slot_check)
    if last_sep < 0:
        die("Cannot find separator in update-binary template")

    # Find the FIRST ui_print that starts the package info block (has separator)
    # Go forward from slot_check backwards: find first ui_print whose snippet contains sep
    import re as _re
    pkg_ui_positions = [m.start() for m in _re.finditer(b'ui_print "', content[:slot_check])]
    open_ui = None
    for pos in pkg_ui_positions:
        if sep.encode() in content[pos:pos+80]:
            open_ui = pos
            break
    if open_ui is None:
        die("Cannot find opening ui_print in update-binary template")

    # Build new block
    lines = [sep]
    lines.append(f"{package_info['name']} | {package_info['device']}")
    ver_parts = []
    if package_info.get("android_ver"):
        ver_parts.append(package_info["android_ver"])
    if package_info.get("bundle"):
        ver_parts.append(package_info["bundle"])
    lines.append(" | ".join(ver_parts) if ver_parts else "")
    if package_info.get("patch_level"):
        lines.append(f"Patch level: {package_info['patch_level']}")
    if package_info.get("build_date"):
        lines.append(f"Build date: {package_info['build_date']}")
    if package_info.get("author"):
        lines.append(f"Installer by {package_info['author']}")
    lines.append(sep)
    # Build new block: each line is a separate ui_print statement
    new_lines = [f'ui_print "{line}"' for line in lines]
    new_block = ("\n".join(new_lines) + "\n").encode()

    # Replace from open_ui to the end of the original package info block
    # The block ends with: ui_print "================================================\"\n
    # We need to find the LAST occurrence of sep+"\"\\n" between open_ui and slot_check
    end_marker = sep_bytes + b'"\n'
    # Search only in the ORIGINAL content (before any replacement)
    end_pos = content.rfind(end_marker, open_ui, slot_check)
    if end_pos < 0:
        # Fallback: use last_sep position
        end_pos = last_sep
    content = content[:open_ui] + new_block + content[end_pos + len(end_marker):]

    return content


# ═══════════════════════════════════════════════════════════════
# 生成 flash 脚本
# ═══════════════════════════════════════════════════════════════

def write_flash_scripts(all_parts: list, output_dir: str, package_info: dict):
    slotted = sorted(SLOTTED_PARTS & set(all_parts))
    sep = "================================================"

    info_lines = [sep,
                  f"{package_info['name']} | {package_info['device']}"]
    ver = []
    if package_info.get("android_ver"): ver.append(package_info["android_ver"])
    if package_info.get("bundle"):    ver.append(package_info["bundle"])
    info_lines.append(" | ".join(ver) if ver else "")
    if package_info.get("author"):    info_lines.append(f"Installer by {package_info['author']}")
    info_lines.append(sep)

    # ── flash_linux.sh ──
    linux_script = f'''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
SLOTTED="{" ".join(slotted)}"
UNPACK=""

cleanup() {{ if [ -n "$UNPACK" ] && [ -d "$UNPACK" ]; then rm -r -- "$UNPACK"; fi; }}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 133' TERM
die() {{ printf '\\nError: %s\\n' "$*" >&2; exit 1; }}
unpack() {{
    echo "Unpacking $1.img..."
    zstd -q --long=27 -d -f "images/$1.img.zst" -o "$UNPACK/$1.img" || die "Unpacking $1.img failed"
}}
flash() {{
    echo "Flashing $1..."
    "$FASTBOOT" flash "$1" "$2" || die "Flashing $1 failed; do not reboot"
}}

'''
    for line in info_lines:
        linux_script += f'echo "{line}"\n'
    linux_script += '''
FASTBOOT=./bin/linux/fastboot
chmod +x "$FASTBOOT" 2>/dev/null || true
"$FASTBOOT" --version >/dev/null 2>&1 || FASTBOOT="$(command -v fastboot || true)"
[ -n "$FASTBOOT" ] || die "fastboot not found"
command -v zstd >/dev/null || die "Install zstd first"
[ -s META-INF/partitions ] || die "META-INF/partitions is missing"
PARTS=()
while IFS= read -r p || [ -n "$p" ]; do
    p="${p%$'\\r'}"
    [ -n "$p" ] || continue
    case "$p" in {" ".join(f'"{p}"' for p in sorted(SLOTTED_PARTS | NON_SLOTTED_PARTS))}) ;;
        *) die "Invalid partition name: $p" ;; esac
    [[ " ${' '.join(PARTS)} " != *" $p "* ]] || die "Duplicate partition: $p"
    [ -s "images/$p.img.zst" ] || die "images/$p.img.zst is missing"
    PARTS+=("$p")
done < META-INF/partitions
[[ " ${' '.join(PARTS)} " == *" super "* ]] || die "super is missing from partition list"

echo "Checking device..."
product="$("$FASTBOOT" getvar product 2>&1 | sed 's/^(bootloader) //' | awk -F': *' '/^product:/{print $2}' | tr -d '\\r ')" || die "Cannot query device"
case "$product" in
    nabu|nabu_global|nabu_in|nabu_cn) echo "Device: $product" ;;
    *) die "Expected nabu, detected: ${product:-none}" ;;
esac
mode="$("$FASTBOOT" getvar is-userspace 2>&1)" || die "Cannot check fastboot mode"
printf '%s\\n' "$mode" | tr -d '\\r' | sed 's/^(bootloader) //' | grep -Eq '^is-userspace: *no$' || die "Use bootloader fastboot, not fastbootd (fastboot reboot bootloader)"

free_kb=$(df -Pk . | awk 'NR==2{print $4}')
[ "${free_kb:-0}" -gt 10500000 ] || die "Need 10 GB free next to this script"
fmt="n"
read -r -p "Format data? This deletes all user data. [y/N]: " fmt || die "No answer received"
UNPACK=$(mktemp -d ./_unpacked_images.XXXXXX) || die "Cannot create temporary directory"
for p in "${PARTS[@]}"; do unpack "$p"; done
if [[ "$fmt" == [yY] ]] && [ -f images/userdata.img.zst ]; then unpack userdata; fi

for p in "${PARTS[@]}"; do
    [ "$p" = super ] && continue
    case " $SLOTTED " in
        *" $p "*) flash "${p}_a" "$UNPACK/$p.img" ;;
        *) flash "$p" "$UNPACK/$p.img" ;;
    esac
done
echo "Flashing super..."
"$FASTBOOT" -S 256M flash super "$UNPACK/super.img" || die "Flashing super failed; do not reboot"
if [[ "$fmt" == [yY] ]]; then
    echo "Formatting data..."
    if [ -f "$UNPACK/userdata.img" ]; then
        flash userdata "$UNPACK/userdata.img"
    elif [ -f images/userdata.img ]; then
        flash userdata images/userdata.img
    elif [ -f userdata.img ]; then
        flash userdata userdata.img
    else
        "$FASTBOOT" erase userdata || die "Erasing userdata failed"
    fi
    "$FASTBOOT" erase metadata || die "Erasing metadata failed"
    "$FASTBOOT" erase frp || die "Erasing FRP failed"
fi
"$FASTBOOT" set_active a || die "Cannot activate slot A; do not reboot"
echo "Slot A selected. Bootloader status:"
"$FASTBOOT" getvar current-slot || die "Cannot read slot state; do not reboot"
"$FASTBOOT" getvar slot-unbootable:a 2>&1 || true
cleanup
UNPACK=""
echo "Rebooting..."
"$FASTBOOT" reboot || die "Reboot failed"
echo "Done."
'''
    ensure_dir(os.path.join(output_dir, "bin", "linux"))
    with open(os.path.join(output_dir, "flash_linux.sh"), "w") as f:
        f.write(linux_script)
    os.chmod(os.path.join(output_dir, "flash_linux.sh"), 0o755)

    # ── flash_windows.bat ──
    info_block_win = "\n".join(f"echo {l}" for l in info_lines)
    win_script = f'''@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set "FASTBOOT=bin\\windows\\fastboot.exe"
set "ZSTD=bin\\windows\\zstd.exe"
set "UNPACK="
set "SLOTTED={ " ".join(slotted) }"

{info_block_win}

if not exist "%FASTBOOT%" goto :missing_tool
if not exist "%ZSTD%" goto :missing_tool
if not exist "META-INF\\partitions" (
    echo Error: META-INF\\partitions is missing.
    goto :fail
)
set "UNPACK=_unpacked_images_%RANDOM%_%RANDOM%"
if exist "%UNPACK%" (
    echo Error: Temporary directory already exists. Run the installer again.
    set "UNPACK="
    goto :fail
)
mkdir "%UNPACK%" || goto :fail
"%FASTBOOT%" --version > "%UNPACK%\\tool.txt" 2>&1
if errorlevel 1 (
    echo Error: Bundled fastboot cannot start. Extract the ENTIRE ZIP first.
    type "%UNPACK%\\tool.txt"
    goto :fail
)
"%ZSTD%" --version > "%UNPACK%\\tool.txt" 2>&1
if errorlevel 1 (
    echo Error: Bundled zstd cannot start. A 64-bit Windows installation is required.
    type "%UNPACK%\\tool.txt"
    goto :fail
)
echo Checking device...
"%FASTBOOT%" getvar product > "%UNPACK%\\device.txt" 2>&1
if errorlevel 1 goto :device_error
set "PRODUCT="
for /f "usebackq delims=" %%l in ("%UNPACK%\\device.txt") do (
    set "LINE=%%l"
    set "LINE=!LINE:(bootloader) =!"
    for /f "tokens=1,2 delims=: " %%a in ("!LINE!") do if /i "%%a"=="product" set "PRODUCT=%%b"
)
set "DEVICE_OK=0"
for %%d in (nabu nabu_global nabu_in nabu_cn) do if "!PRODUCT!"=="%%d" set "DEVICE_OK=1"
if "!DEVICE_OK!"=="0" goto :device_error
echo Device: !PRODUCT!
"%FASTBOOT%" getvar is-userspace > "%UNPACK%\\mode.txt" 2>&1
if errorlevel 1 goto :mode_error
set "MODE="
for /f "usebackq delims=" %%l in ("%UNPACK%\\mode.txt") do (
    set "LINE=%%l"
    set "LINE=!LINE:(bootloader) =!"
    for /f "tokens=1,2 delims=: " %%a in ("!LINE!") do if /i "%%a"=="is-userspace" set "MODE=%%b"
)
if /i not "!MODE!"=="no" goto :mode_error

set "FMT=N"
set /p FMT=Format data? This deletes all user data. [y/N]:
for /f "usebackq delims=" %%p in ("META-INF\\partitions") do (
    call :unpack %%p
    if errorlevel 1 goto :fail
)
if /i "!FMT!"=="y" if exist "images\\userdata.img.zst" (
    call :unpack userdata
    if errorlevel 1 goto :fail
)

for /f "usebackq delims=" %%p in ("META-INF\\partitions") do (
    if not "%%p"=="super" (
        set "IS_SLOTTED=0"
        for %%s in (%SLOTTED%) do if "%%p"=="%%s" set "IS_SLOTTED=1"
        if "!IS_SLOTTED!"=="1" (
            call :flash %%p_a "%UNPACK%\\%%p.img"
            if errorlevel 1 goto :fail
        ) else (
            call :flash %%p "%UNPACK%\\%%p.img"
            if errorlevel 1 goto :fail
        )
    )
)
echo Flashing super...
"%FASTBOOT%" -S 256M flash super "%UNPACK%\\super.img" || goto :fail
if /i "!FMT!"=="y" (
    echo Formatting data...
    if exist "%UNPACK%\\userdata.img" (
        call :flash userdata "%UNPACK%\\userdata.img"
        if errorlevel 1 goto :fail
    ) else if exist "images\\userdata.img" (
        call :flash userdata "images\\userdata.img"
        if errorlevel 1 goto :fail
    ) else if exist "userdata.img" (
        call :flash userdata "userdata.img"
        if errorlevel 1 goto :fail
    ) else (
        "%FASTBOOT%" erase userdata || goto :fail
    )
    "%FASTBOOT%" erase metadata || goto :fail
    "%FASTBOOT%" erase frp || goto :fail
)
"%FASTBOOT%" set_active a || goto :fail
echo Slot A selected. Bootloader status:
"%FASTBOOT%" getvar current-slot
"%FASTBOOT%" getvar slot-unbootable:a
rd /s /q "%UNPACK%"
set "UNPACK="
echo Rebooting...
"%FASTBOOT%" reboot || goto :fail
echo Done.
pause
exit /b 0

:unpack
echo Unpacking %~1.img...
"%ZSTD%" -q --long=27 -d -f "images\\%~1.img.zst" -o "%UNPACK%\\%~1.img"
exit /b %errorlevel%

:flash
echo Flashing %~1...
"%FASTBOOT%" flash %~1 "%~2"
exit /b %errorlevel%

:missing_tool
echo Error: fastboot.exe or zstd.exe is missing.
goto :fail
:device_error
echo Error: Connect Xiaomi Pad 5 in bootloader fastboot mode.
if defined UNPACK if exist "%UNPACK%\\device.txt" type "%UNPACK%\\device.txt"
goto :fail
:mode_error
echo Error: Use bootloader fastboot, not fastbootd.
echo Run: fastboot reboot bootloader
if defined UNPACK if exist "%UNPACK%\\mode.txt" type "%UNPACK%\\mode.txt"
goto :fail
:fail
echo.
echo Installation failed. Do not reboot if flashing was interrupted.
if defined UNPACK if exist "%UNPACK%" rd /s /q "%UNPACK%"
pause
exit /b 1
'''
    with open(os.path.join(output_dir, "flash_windows.bat"), "w") as f:
        f.write(win_script)

    log(f"  Generated flash scripts ({len(all_parts)} partitions)")


# ═══════════════════════════════════════════════════════════════
# 组装 ZIP
# ═══════════════════════════════════════════════════════════════

def assemble_zip(output_zip: str, all_parts: list, img_results: list,
                 work_dir: str, card_dir: str, package_info: dict):
    """
    Single-pass ZIP assembly with correct offset tracking.
    Strategy:
      1. Write all files EXCEPT update-binary into a temp ZIP.
      2. Read the Central Directory to find the offset where update-binary WILL go.
      3. Generate update-binary with those correct offsets.
      4. Write update-binary first (it goes at the end in CD order), then rewrite.
         Actually: we write update-binary LAST so its offset is known from step 2.
         But ZIP local headers are written in order... So we need a different approach.

    Better approach:
      - Collect all non-update-binary file data
      - Write them to a temp buffer, record their offsets via CD
      - The update-binary offset = end of last file's data + header overhead
      - Generate update-binary with these offsets
      - Write final ZIP: update-binary first (since it has a fixed-size local header
        we control), then all other files in sorted order
      - But this changes the order... The LAYOUT in update-binary uses absolute
        byte offsets within the ZIP, so order matters.

    Final approach: two-pass with fixed-size placeholder.
      - Pass 1: write everything with a LARGE placeholder for update-binary
      - Pass 2: generate real update-binary with correct offsets from Pass 1
      - Pass 3: write final ZIP, replacing placeholder with real content
        (real content must fit in placeholder; if not, pad/truncate)

    Actually the cleanest: make placeholder large enough, write once, patch update-binary
    in-place by seeking to its local header and rewriting. But ZIP local headers have
    variable-length filename+extra fields, making in-place patching tricky.

    SIMPLEST correct approach:
      1. Compute what the update-binary WILL contain (we can estimate size)
      2. Use a placeholder of that estimated size
      3. Write ZIP, read offsets
      4. Generate real update-binary, compare size
      5. If sizes match exactly -> done
      6. If not -> iterate with adjusted placeholder size
    """
    template_ub = os.path.join(card_dir, "META-INF", "com", "google", "android", "update-binary")
    if not os.path.exists(template_ub):
        die(f"Template update-binary not found: {template_ub}")

    # Collect all non-update-binary file data
    file_data = {}  # arcname -> bytes

    # Tool binaries
    for tool_name in ("zstd", "recovery-io", "bootctl"):
        p = os.path.join(card_dir, "META-INF", tool_name)
        if os.path.exists(p):
            file_data[f"META-INF/{tool_name}"] = open(p, "rb").read()

    # META-INF/partitions
    parts_text = "\n".join(sorted(all_parts)) + "\n"
    file_data["META-INF/partitions"] = parts_text.encode()

    # updater-script (empty)
    file_data["META-INF/com/google/android/updater-script"] = b""

    # boot.orangefox.json
    orbx = os.path.join(card_dir, "META-INF", "boot.orangefox.json")
    if os.path.exists(orbx):
        file_data["META-INF/boot.orangefox.json"] = open(orbx, "rb").read()

    # Image files
    for part_name, zst_path, comp_size, crc, raw_size in sorted(img_results):
        arcname = f"images/{part_name}.img.zst"
        if comp_size > 0:
            file_data[arcname] = open(zst_path, "rb").read()
        else:
            file_data[arcname] = b""

    # bin/ tools
    for root, _, fnames in os.walk(os.path.join(card_dir, "bin")):
        for fn in sorted(fnames):
            full = os.path.join(root, fn)
            arcname = os.path.relpath(full, card_dir)
            file_data[arcname] = open(full, "rb").read()

    # Estimate update-binary size (template ~6649 bytes, LAYOUT adds ~2-3 KB for 30 entries)
    # Each layout line: "name offset size crc raw  \n" ~ 50-80 chars
    estimated_ub_size = 6649 + len(all_parts) * 70 + 200  # rough estimate
    # Round up to nearest 512 for alignment
    estimated_ub_size = ((estimated_ub_size // 512) + 1) * 512

    # Iterative offset computation
    max_iterations = 5
    for iteration in range(max_iterations):
        ub_placeholder = b"\x00" * estimated_ub_size

        # Build file list with current placeholder size
        all_files = dict(file_data)
        all_files["META-INF/com/google/android/update-binary"] = ub_placeholder

        # Write to temp buffer to compute offsets
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for arcname in sorted(all_files.keys()):
                data = all_files[arcname]
                if len(data) > 0:
                    zf.writestr(arcname, data)
                else:
                    zi = zipfile.ZipInfo(arcname)
                    zf.writestr(zi, b"")
        buf.seek(0)

        # Read offsets
        file_offsets = {}
        with zipfile.ZipFile(buf, "r") as zf:
            for info in zf.infolist():
                fn = info.filename.encode()
                data_start = info.header_offset + 30 + len(fn) + len(info.extra)
                file_offsets[info.filename] = (data_start, info.compress_size, info.file_size)

        # Generate update-binary
        tool_layout = []
        for tool_name, arcname in [("zstd", "META-INF/zstd"),
                                    ("io", "META-INF/recovery-io"),
                                    ("bootctl", "META-INF/bootctl")]:
            if arcname in file_offsets:
                ds, cs, _ = file_offsets[arcname]
                src = os.path.join(card_dir, *arcname.split("/"))
                crc_val = crc32_file(src) if os.path.exists(src) else 0
                tool_layout.append((tool_name, ds, cs, crc_val, 0))

        img_layout = []
        for part_name, zst_path, comp_size, crc, raw_size in img_results:
            arcname = f"images/{part_name}.img.zst"
            if arcname in file_offsets:
                ds, cs, _ = file_offsets[arcname]
                img_layout.append((part_name, ds, cs, crc, raw_size))

        all_layout = sorted(tool_layout + img_layout, key=lambda x: x[1])
        update_binary = build_update_binary(template_ub, all_layout, package_info)

        actual_ub_size = len(update_binary)
        log(f"  Iteration {iteration+1}: placeholder={estimated_ub_size}, actual={actual_ub_size}")

        if actual_ub_size <= estimated_ub_size:
            # Check if we can fit without truncation
            # Pad to placeholder size
            padded_ub = update_binary + b"\x00" * (estimated_ub_size - actual_ub_size)
            break
        else:
            # Need larger placeholder - increase and retry
            estimated_ub_size = actual_ub_size + 512
    else:
        # Fallback: use exact size, write in two passes
        log(f"  Using exact-size approach (placeholder={estimated_ub_size}, actual={actual_ub_size})")
        padded_ub = update_binary  # Will need special handling

    # ── Write final ZIP ──
    # Strategy: write update-binary LAST so we know its offset exactly
    # (ZIP writes local headers in order, CD references them)
    # Actually we can write in any order; CD has absolute offsets.
    # Write update-binary with its FINAL correct offset.

    # Get final offsets with the actual update-binary
    all_files_final = dict(file_data)
    all_files_final["META-INF/com/google/android/update-binary"] = padded_ub

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname in sorted(all_files_final.keys()):
            data = all_files_final[arcname]
            if len(data) > 0:
                zf.writestr(arcname, data)
            else:
                zi = zipfile.ZipInfo(arcname)
                zf.writestr(zi, b"")

    final_data = buf2.getvalue()
    with open(output_zip, "wb") as f:
        f.write(final_data)

    # Verify
    with zipfile.ZipFile(output_zip, "r") as zf:
        names = zf.namelist()
        log(f"  ZIP entries: {len(names)}")
        ub_info = zf.getinfo("META-INF/com/google/android/update-binary")
        log(f"  update-binary: offset={ub_info.header_offset}, size={ub_info.compress_size}")
        parts_info = zf.getinfo("META-INF/partitions")
        log(f"  partitions: offset={parts_info.header_offset}, size={parts_info.compress_size}")

    # Cleanup temp zst files
    for _, zst_path, _, _, _ in img_results:
        if os.path.exists(zst_path):
            os.unlink(zst_path)

    log(f"  ZIP size: {len(final_data):,} bytes")
    log(f"  Entries: {len(all_layout)} ({len(tool_layout)} tools + {len(img_layout)} images)")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Nabu Card+Fastboot Package Builder")
    parser.add_argument("--line-zip",  help="Line flash package (.zip) path")
    parser.add_argument("--card-dir",  help="Card flash template directory")
    parser.add_argument("--output",    help="Output directory")
    parser.add_argument("--ratio",     type=float, default=COMPRESSION_RATIO,
                        help=f"Compression ratio estimate (default {COMPRESSION_RATIO})")
    parser.add_argument("--dry-run",   action="store_true", help="Show plan only")
    # Package info (override defaults / read from env)
    parser.add_argument("--package-name",   default=os.environ.get("PACKAGE_NAME"),   help="Package name")
    parser.add_argument("--device-name",    default=os.environ.get("DEVICE_NAME"),    help="Device name")
    parser.add_argument("--android-ver",    default=os.environ.get("ANDROID_VER"),    help="Android version")
    parser.add_argument("--bundle-number",  default=os.environ.get("BUNDLE_NUMBER"),  help="Build number")
    parser.add_argument("--patch-level",    default=os.environ.get("PATCH_LEVEL"),    help="Security patch level")
    parser.add_argument("--build-date",     default=os.environ.get("BUILD_DATE"),     help="Build date")
    parser.add_argument("--author",         default=os.environ.get("AUTHOR"),         help="Author")
    args = parser.parse_args()

    # Apply CLI overrides to module-level config
    if args.package_name:   globals()['PACKAGE_NAME']   = args.package_name
    if args.device_name:    globals()['DEVICE_NAME']    = args.device_name
    if args.android_ver:    globals()['ANDROID_VER']    = args.android_ver
    if args.bundle_number:  globals()['BUNDLE_NUMBER']  = args.bundle_number
    if args.patch_level:    globals()['PATCH_LEVEL']    = args.patch_level
    if args.build_date:     globals()['BUILD_DATE']     = args.build_date
    if args.author:         globals()['AUTHOR']         = args.author
    globals()['COMPRESSION_RATIO'] = args.ratio

    script_dir = os.path.dirname(os.path.abspath(__file__))
    card_dir   = args.card_dir or os.path.join(script_dir, CARD_LOCAL)
    output_dir = args.output   or os.path.join(script_dir, OUTPUT_DIR)
    work_dir   = tempfile.mkdtemp(prefix="nabu_build_")
    img_dir    = os.path.join(work_dir, "images")
    ensure_dir(img_dir)
    ensure_dir(output_dir)

    log("=" * 60)
    log("Nabu Card+Fastboot Package Builder")
    log("=" * 60)

    # ── Get line flash package ──
    lineflash_source = None
    use_directory = False

    if args.line_zip:
        if os.path.isdir(args.line_zip):
            use_directory = True
            lineflash_source = args.line_zip
        elif os.path.isfile(args.line_zip):
            lineflash_source = args.line_zip
            log(f"Line flash: {lineflash_source} ({os.path.getsize(lineflash_source):,} bytes)")
        else:
            die(f"Line flash not found: {args.line_zip}")
    else:
        # Try local zip first
        local_zip = os.path.join(LINE_LOCAL, "lineflash.zip")
        if os.path.exists(local_zip):
            lineflash_source = local_zip
            log(f"Line flash: {lineflash_source} ({os.path.getsize(lineflash_source):,} bytes)")
        elif LINE_DOWNLOAD:
            download_file(LINE_DOWNLOAD, local_zip)
            lineflash_source = local_zip
        elif os.path.isdir(LINE_LOCAL):
            use_directory = True
            lineflash_source = LINE_LOCAL
            log(f"Using directory mode: {lineflash_source}")
        else:
            die("Please provide line flash via --line-zip or LINE_DOWNLOAD")

    if use_directory and lineflash_source:
        log(f"Using directory mode: {lineflash_source}")

    # ── Parse line flash ──
    images = extract_lineflash(lineflash_source or LINE_LOCAL, img_dir, use_directory)
    log(f"Extracted {len(images)} partition images")

    # ── Confirm partition list ──
    all_parts = sorted(set(list(images.keys())))
    if "super" not in images:
        super_path = os.path.join(img_dir, "super.img")
        open(super_path, "wb").close()
        images["super"] = super_path
        all_parts.append("super")
        all_parts.sort()
        log("  super.img -> created empty image")

    unknown = [p for p in all_parts if p not in ALL_KNOWN_PARTS]
    if unknown:
        log(f"  Warning: unknown partitions (will use non-slotted): {unknown}")

    # ── Compress ──
    log("Compressing images (zstd --long=27) ...")
    img_results = compress_images(images, img_dir)

    # ── Space check ──
    total_comp = sum(s for _, _, s, _, _ in img_results)
    tool_size = sum(os.path.getsize(os.path.join(card_dir, "META-INF", t))
                    for t in ("zstd", "recovery-io", "bootctl")
                    if os.path.exists(os.path.join(card_dir, "META-INF", t)))
    total_estimated = int((total_comp + tool_size) / COMPRESSION_RATIO)
    free_kb = shutil.disk_usage(".").free // 1024
    needed_kb = total_estimated // 1024
    log(f"  Compressed total: {total_comp:,} bytes")
    log(f"  Estimated raw space (x{1/COMPRESSION_RATIO:.2f}): {total_estimated:,} bytes ({needed_kb:,} KiB)")
    log(f"  Available:          {free_kb:,} KiB")
    if free_kb < needed_kb:
        die(f"Insufficient space! Need ~{needed_kb:,} KiB, have {free_kb:,} KiB")

    # ── Dry run ──
    if args.dry_run:
        log("-- DRY RUN: showing plan only --")
        log(f"Partition list ({len(all_parts)}):")
        for p in all_parts:
            tag = " [A/B]" if p in SLOTTED_PARTS else ""
            sz = os.path.getsize(images[p]) if images.get(p) and os.path.exists(images[p]) else 0
            log(f"  {p:20s}{tag}  {sz:>12,} bytes")
        log(f"Estimated ZIP size: ~{total_estimated:,} bytes ({total_estimated//1024//1024} MiB)")
        return

    # ── Package info ──
    package_info = {
        "name": PACKAGE_NAME,
        "device": DEVICE_NAME,
        "android_ver": ANDROID_VER,
        "bundle": BUNDLE_NUMBER,
        "patch_level": PATCH_LEVEL,
        "build_date": BUILD_DATE,
        "author": AUTHOR,
    }

    # ── Generate flash scripts ──
    log("Generating flash scripts ...")
    write_flash_scripts(all_parts, output_dir, package_info)

    # ── Assemble ZIP ──
    out_name = OUTPUT_NAME or ""
    if not out_name:
        ts = datetime.datetime.now().strftime("%Y%m%d")
        out_name = f"{PACKAGE_NAME.replace(' ','_')}_{DEVICE_NAME}_{ANDROID_VER}_{ts}.zip"
    output_zip = os.path.join(output_dir, out_name)

    log(f"Assembling final ZIP: {output_zip}")
    assemble_zip(output_zip, all_parts, img_results, work_dir, card_dir, package_info)

    log("=" * 60)
    log(f"Done: {output_zip}")
    log(f"  ZIP size: {os.path.getsize(output_zip):,} bytes")
    log(f"  Partitions: {len(all_parts)}")
    log("=" * 60)


if __name__ == "__main__":
    main()
