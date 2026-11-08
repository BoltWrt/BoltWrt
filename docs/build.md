# 固件构建

## 构建输入

从所需版本的 BoltWrt 源码建立干净的构建目录。仓库已包含完整 OpenWrt 源码。4.0.0 基线为 OpenWrt 25.12.6、Linux 6.12.112、GCC 14.3.0 和 binutils 2.44。 [源码锁](../config/source-lock.json)

- 同步维护 `config/source-lock.json` 和 `feeds.conf.default` 中的 feed 地址和提交。发布版本号同时写入源码锁及 `CONFIG_VERSION_NUMBER`。 [Feed 配置](../feeds.conf.default) · [版本配置](../config/version.txt)
- 准备脚本按锁检出 feeds 和子模块，按二进制包名识别重叠来源，优先注册 BoltWrt provider，并应用固定兼容改动。锁不匹配或发现未知修改时会停止。更新锁或切换版本时使用新的构建目录。 [准备脚本](../scripts/boltwrt-prepare.py)
- 产品配置由 `config/version.txt`、`config/common.txt` 和对应的 `config/targets/<目标>.txt` 合成。修改功能时更新相应片段，再由 `make defconfig` 解析依赖。冲突或未保留的必需选项会报错。 [公共配置](../config/common.txt) · [目标片段](../config/targets/) · [配置脚本](../scripts/boltwrt-config.py)

验证本地 Packages 候选时，先在测试构建目录中更新锁和 feed 配置，再为准备脚本传入 `--local-feed /path/to/Packages`。本地仓库须含锁定的提交对象，工作区中未提交的改动不会被构建读取。子模块仍按各自锁定的地址与提交准备。

## 本地构建

使用普通用户在 Linux 构建环境中运行，构建目录绝对路径须无空格，校验脚本需要 Python 3.11 或更新版本。GitHub Actions 使用 Ubuntu 24.04 amd64，构建依赖列表见准备 action，其中 `gcc-multilib` 和 `g++-multilib` 对应 amd64 宿主。x86 镜像静态检查还使用 `qemu-img`，由 `qemu-utils` 提供。完整构建包含 Go、Rust 和 LLVM，按可用内存和磁盘选择并行度。两个产品目标各用独立构建目录。 [CI 准备 action](../.github/actions/prepare-firmware/action.yml)

在固件仓库根目录执行。构建 RAX 时将 `target` 改为 `cmcc-rax3000m`。

```sh
set -e
target=x86-64
jobs=2

python3 scripts/boltwrt-prepare.py
python3 scripts/boltwrt-config.py "$target"
DOWNLOAD_MIRROR=https://sources.cdn.openwrt.org make download -j"$jobs" V=s
make -j"$jobs" CONFIG_AUTOREMOVE=y
python3 scripts/boltwrt-verify.py "$target"
```

下载按 recipe 校验来源。Go、Cargo 和其他宿主构建工具还可能在编译阶段取用各自的固定输入。并行编译失败时可用 `make -j1 V=s CONFIG_AUTOREMOVE=y` 定位实际失败命令。

在 ARM64 Linux 宿主上，使用满足构建要求的已核验外部 Go 作 bootstrap。以下两条命令替换上面的 download 和 build，其余步骤相同。4.0.0 的实际产品编译器仍由准备脚本固定为 Go 1.26.10。

```sh
go_root="$(GOTOOLCHAIN=local go env GOROOT)" || exit 1
GOTOOLCHAIN=local "$go_root/bin/go" version || exit 1
DOWNLOAD_MIRROR=https://sources.cdn.openwrt.org GOTOOLCHAIN=local \
  make download -j"$jobs" V=s CONFIG_GOLANG_BUILD_BOOTSTRAP= \
  CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="$go_root"
GOTOOLCHAIN=local make -j"$jobs" V=s CONFIG_AUTOREMOVE=y \
  CONFIG_GOLANG_BUILD_BOOTSTRAP= CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="$go_root"
```

## 产物与检查

| 目标参数 | 产物目录 | 主要产物 |
| --- | --- | --- |
| `x86-64` | `bin/targets/x86/64/` | BIOS、UEFI 各有一份 SquashFS 整盘 IMG.gz 和 VMDK |
| `cmcc-rax3000m` | `bin/targets/mediatek/filogic/` | 系统升级 FIT、精简恢复 FIT、eMMC GPT 以及各介质／DDR 组合的 preloader 和 FIP |

- 准备脚本写入 `logs/prepare-sources.json`，记录实际 feed、子模块、补丁摘要和 provider。配置与产物检查分别写入 `logs/boltwrt/<目标>-config.json` 和 `logs/boltwrt/<目标>-images.json`。
- 静态校验核对产品版本、实际 manifest、镜像与 profiles 中的摘要，以及 x86 磁盘布局或 RAX 的 FIT 容量与启动文件。对已导出的 RAX 文件可用 `python3 scripts/boltwrt-verify.py cmcc-rax3000m --rax-budget-only --directory "$image_dir"` 单独检查 GPT/FIT 容量，其中 `image_dir` 指向同一版本的产物目录。 [产物校验脚本](../scripts/boltwrt-verify.py)
- 准备、配置和镜像校验脚本的回归入口为 `python3 -B -m unittest discover -s scripts/tests -p 'test_boltwrt_*.py' -v`。实际启动、软件包操作和升级使用独立运行用例。 [运行测试](../tests/runtime/README.md)

## GitHub Actions

手动运行 `Build Firmware` 可构建两个产品目标。每个目标先准备工作区，从缓存恢复 `dl/`（如启用），再按锁定配置下载并校验源码。编译 job 按预取 job 提供的 Artifact ID 下载源码包，校验提交、目标、锁和配置身份，再还原 `dl/` 并编译。构建结果与检查记录分别上传为 `BoltWrt-Firmware_<目标>` 和 `BoltWrt-Checks_<目标>`。 [目标构建工作流](../.github/workflows/build-firmware-target.yml)

`cold_source_cache` 用于跳过本次预取的缓存恢复。`debug_on_failure` 仅在手动请求且 job 失败时打开限触发者访问的调试会话。发布工作流由匹配 `v*.*.*` 的标签推送触发，双目标构建通过后分别打包为 `BoltWrt_<目标>_<标签>.tar.gz` 并上传 GitHub Releases。 [工作流入口](../.github/workflows/build-firmware.yml)
