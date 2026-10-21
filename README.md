# Nabu Flash Package Builder / Nabu 刷机包构建工具

Automated card+fastboot flash package builder for Xiaomi Pad 5 (nabu).
小米平板5 (nabu) 的卡线一体刷机包自动化构建工具。

## Features / 功能特性

- Extracts all partitions from line flash packages / 从线刷包提取所有分区
- Compresses images with zstd (`--long=27`) / 使用zstd压缩镜像
- Embeds fastboot, zstd, recovery-io, bootctl in ZIP / 嵌入式工具
- Generates LAYOUT for OrangeFox recovery / 生成OrangeFox兼容的LAYOUT
- Includes fastboot scripts (Linux/Windows) / 包含快刷脚本

## Usage / 用法

### GitHub Actions

Trigger `workflow_dispatch` and fill in the required inputs.
触发 `workflow_dispatch` 并填写必填参数。

| Input | Required / 必填 | Default | Description / 说明 |
|-------|----------------|---------|-------------------|
| `line_zip_url` | Yes / 是 | | Line flash package .zip URL / 线刷包下载链接 |
| `card_zip_url` | No / 否 | | Card flash template .zip URL / 卡刷模板下载链接 |
| `package_name` | Yes / 是 | `LineageOS 23.2` | Package name / 包名 |
| `device_name` | Yes / 是 | `Xiaomi Pad 5 (nabu)` | Device name / 机型 |
| `android_ver` | Yes / 是 | `Android 13` | Android version / Android版本 |
| `bundle_number` | No / 否 | | Build number / 构建号 |
| `patch_level` | No / 否 | | Security patch level / 安全补丁级别 |
| `build_date` | No / 否 | | Build date / 构建日期 |
| `author` | No / 否 | | Installer author / 作者 |
| `compression_ratio` | No / 否 | `0.60` | Compression ratio estimate / 压缩率估算 |
| `release_tag` | No / 否 | | GitHub Release tag (e.g. v1.0) / Release标签 |

### Local

```bash
python3 build.py --line-zip ./lineflash.zip --card-dir ./card_template/
```

## Output / 输出

The output ZIP contains:
- Card flash compatible with OrangeFox/TWRP recovery
- `flash_linux.sh` and `flash_windows.bat` for fastboot flashing
- Embedded tools: fastboot, zstd, recovery-io, bootctl

输出ZIP包含：
- 兼容OrangeFox/TWRP恢复模式的卡刷包
- 快刷用的 flash_linux.sh 和 flash_windows.bat
- 嵌入工具：fastboot、zstd、recovery-io、bootctl

## Supported Partitions / 支持分区

30 partitions including QCOM bootloader, radio, DSP, and system images.
30个分区，包括QCOM引导加载程序、无线电、DSP和系统镜像。

---

*Built with ❤️ for the nabu community*
*为 nabu 社区构建*
