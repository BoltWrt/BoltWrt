# BoltWrt

BoltWrt 基于 OpenWrt 正式稳定版，迁回必要定制并维护平台配置、构建流程和升级兼容性。4.0.0 的基线是 OpenWrt **v25.12.6**，固定 commit `64900e756efa667774214f9c6b17c113222e01f7`，Linux **6.12.112**。这是从 24.10 / Linux 6.6 到 25.12 / Linux 6.12 的基础平台迁移，包管理器采用 APK，Wi-Fi 使用上游 ucode 实现。

firewall4/nftables、WireGuard 和双平台支持是已有能力。4.0.0 保留 DNS、DDNS、透明代理、SQM、UPnP、OpenVPN、ocserv、frpc 等功能，并通过新版本依赖解析和最终固件 manifest 核对实际前后端。

## 构建

使用具备 OpenWrt 构建依赖的 Linux、普通用户和区分大小写的文件系统。不要在主机上以 root 编译。macOS 上可在 Docker 的 Linux volume 内构建，不应直接使用大小写不敏感的目录作为构建区。

```sh
python3 scripts/boltwrt-prepare.py
python3 scripts/boltwrt-config.py x86-64
DOWNLOAD_MIRROR=https://sources.cdn.openwrt.org make download -j6
make -j4 V=s
python3 scripts/boltwrt-verify.py x86-64
```

源码归档优先使用 OpenWrt 官方缓存；缓存未命中时，下载器继续使用各 recipe 的源地址。全部归档仍须符合固定校验值。

顶层 `make download` 不覆盖所有 host 构建依赖；实际编译仍可能补取固定 recipe 的输入，不能据该命令返回 0 推定离线构建已具备全部源码。

在 ARM64 Linux 构建宿主上，上游 Go 1.4 自举起点不可用。可使用宿主已有、满足 [Go 1.26.10 自举要求](https://github.com/golang/go/blob/go1.26.10/src/make.bash)的 Go（至少 1.24.6），在每次编译命令中同时取消内部 bootstrap 并指定其根目录。准备脚本在固定 feed 上精确应用 Go 1.26.10 安全补丁版及官方源码散列，修复 [GO-2026-6809](https://pkg.go.dev/vuln/GO-2026-6809)，不滚动升级 feed。此前产物实际使用 Go 1.26.9，更新后的 Go 程序仍须重新构建验收；镜像内 Go 1.26.8 仅为 bootstrap：

```sh
go_root="$(GOTOOLCHAIN=local go env GOROOT)" || exit 1
GOTOOLCHAIN=local "$go_root/bin/go" version
GOTOOLCHAIN=local make -j4 V=s CONFIG_GOLANG_BUILD_BOOTSTRAP= \
  CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="$go_root"
```

保存外部 bootstrap 版本、构建环境身份及有效命令参数；这些覆盖不包含在 `.config` SHA 中。Ubuntu amd64 CI 保持上游原生自举链。

RAX3000M/Me 使用独立干净构建目录，把目标参数换为 `cmcc-rax3000m`。准备脚本按 `config/source-lock.json` 抓取完整 SHA，验证子模块与已知补丁；不依赖旧版 Go/Python 覆盖或已有下载缓存。重复执行接受已知幂等补丁，遇到源版本、补丁锚点、未知修改或额外包提供者漂移时失败并保留原文件。`defconfig` 后逐项核对产品选择，阻止静默丢失组件或仅有 LuCI 页面而无后端。

自定义 feed 的锁定提交 `af74fe86ff3b8729b48d1977a9032ab7946014e2` 已可从公开的 `BoltWrt/Packages` 仓库取得，干净环境可直接使用默认准备命令。若使用已有的本地源码镜像，可显式指定含该提交的 Packages 仓库：

```sh
python3 scripts/boltwrt-prepare.py --local-feed /absolute/path/to/Packages
```

该入口仅替换抓取位置，仍核对同一个 commit 和全部子模块 SHA。官方五项 feeds、工具链版本及自定义 feed/gitlink 固定值均见 `config/source-lock.json`。准备与配置证据写入忽略目录 `logs/`；实际固件在 `bin/targets/x86/64/` 或 `bin/targets/mediatek/filogic/`。

## 平台和默认配置

| 平台 | 镜像与差异 |
| --- | --- |
| x86-64 | BIOS / UEFI；SquashFS IMG.gz 与 VMDK；32 MiB boot、384 MiB rootfs；Zabbix 及额外 DDNS providers |
| RAX3000M/Me | Filogic；NAND / eMMC；DDR3 / DDR4 各自的启动文件；initramfs recovery FIT 与 sysupgrade FIT；USB 存储及 Samba |

RAX 各变体需匹配实际存储、DDR 和设备布局，不能互换 bootloader。共同 sysupgrade 镜像须小于保守的 104 MiB NAND 包体预算；这不替代坏块、UBI 余量及刷写实机验证。硬件映射和阈值依据见 [平台迁移说明](docs/4.0.0-platform.md)。

新装 LAN 为 `10.126.0.1/18`，默认 hostname 为 BoltWrt，使用预设 NTP；Web 由 nginx HTTPS、uwsgi、LuCI 和 Argon 提供。保留升级中的用户网络、主题与现有证书，不启用第二套 uhttpd 服务。

## 验证与升级边界

4.0.0 正在开发与验收。容器配置语义测试、源码审查、编译、镜像检查和实机运行是不同证据。编译或静态检查通过不能证明 DHCP、PPPoE、IPv6 PD、DNS、代理、SQM、VPN、无线或保留配置升级可用。

本次升级验收范围仅为 **3.1.2 → 4.0.0**。旧 opkg/APK 状态、旧包二进制、内核模块及整个 overlay 不应迁移；需要保留的 UCI、自定义脚本、证书及用户数据必须按实际备份清单核对。阿里云 DDNS 的旧域名格式有专门迁移，见平台说明。2.x、3.0.x 等版本直升兼容性另行验证。

自有 APK 仓库、自动更新服务、发布资产完善、新设备和新 UI 不属于本次迁移范围。上游 APK 源不能提供 BoltWrt 自定义包，也不能保证匹配自编内核的模块；不得把上游更新能力视为定制固件已自动适配。

2026-10-26 用户将后续运行验收缩减为云端虚拟环境中的启动、默认配置、APK/LuCI 和受控 3.1.2 升级；取消项及 ARM 仿真边界见 [云端验收交接](docs/4.0.0-cloud-validation.md)。该文档可供没有本机会话上下文的新云端会话直接执行；它不表示已完成云端测试或 RAX 板级启动。

## 来源与许可证

- [OpenWrt](https://github.com/openwrt/openwrt)
- [BoltWrt Packages](https://github.com/BoltWrt/Packages)
- [Argon](https://github.com/jerrykuku/luci-theme-argon)
- [SmartDNS](https://github.com/pymumu/smartdns)

遵循 [GPL-2.0](COPYING) 及各组件各自的许可证。
