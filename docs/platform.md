# 平台与升级兼容

## 平台与镜像

| 目标 | 镜像与平台配置 |
| --- | --- |
| x86-64 | SquashFS 整盘 IMG.gz 和 VMDK 均有 BIOS／UEFI 两种版本。内核分区为 32 MiB，rootfs 分区为 384 MiB。预装支持 TLS 的 `zabbix-agentd-basic`，以及 Cloudflare、GoDaddy、华为云 DDNS provider。 |
| CMCC RAX3000M / RAX3000Me | 共用 MT7981B profile，提供 NAND／eMMC overlays 和 DDR3／DDR4 对应的 preloader、FIP。生产 FIT 用于系统升级，独立精简 initramfs FIT 用于恢复。固件包含 USB 存储、Samba、wsdd2 及 RAX 无线驱动。 |

RAX 的 `iwinfo` 由 `wifi-scripts` 提供 ucode 实现，依赖旧 CLI 输出格式的脚本需要核对解析逻辑。引导文件须匹配实际闪存和 DDR 类型。镜像定义、安装步骤和恢复入口分别见下列文件。 [目标配置](../config/targets/) · [RAX 镜像定义](../target/linux/mediatek/image/filogic.mk) · [用户指南](../README.md)

### RAX 恢复镜像与容量

恢复系统使用独立软件包清单，保留启动、存储、网络和刷写工具，生产服务随升级 FIT 交付。 [恢复包清单](../config/recovery/cmcc-rax3000m.txt) · [恢复根文件系统检查](../scripts/boltwrt-recovery.py)

NAND 的原始 UBI 区域为 114 MiB。构建校验以生产 FIT 和恢复 FIT 的完整文件字节数，加上两个各 1 MiB 的环境卷，检查总量严格小于 104 MiB。eMMC 按 GPT 分区分别校验两个 FIT 的扇区占用，生产分区为 128 MiB，恢复分区为 32 MiB。实际 NAND 容量还取决于 UBI 开销、保留块、坏块及设备状态。 [FIT 容量校验](../scripts/boltwrt-verify.py) · [NAND 分区定义](../target/linux/mediatek/dts/mt7981b-cmcc-rax3000m-nand.dtso)

## 新装与保留配置

| 设置 | 新装 | 标准保留配置升级 |
| --- | --- | --- |
| LAN、主机名与 NTP | LAN 为 `10.126.0.1/18`，主机名为 `BoltWrt`，生成预设的 NTP 列表。 | 先恢复配置再生成缺失的 UCI 文件，原有 `network` 和 `system` 继续使用。 |
| Web | LuCI 使用 Nginx、uWSGI 和 Nginx ubus 模块，默认 Argon 主题。首次 HTTPS 访问使用设备生成的自签证书。 | 备份包含的主题、证书和 Web 配置由升级流程恢复。4.0.0 固件附带 Argon。 |
| 独立 HAProxy 服务 | `09-boltwrt-haproxy` 禁用独立 init。PassWall 2 仍可按自身配置调用 HAProxy。 | 有标准备份恢复的 `10_disable_services` 时，由该脚本恢复服务启用意图。无该标记的手工备份按新装默认处理。 |

[发行版默认脚本](../package/boltwrt-defaults/files/etc/) · [LuCI 与 Nginx 准备逻辑](../scripts/boltwrt-prepare.py)

## 从 3.1.2 升级

- **备份范围**。用旧系统的 `sysupgrade -l` 核对待保留文件。UCI 中引用的外部证书、密钥或脚本如果不在清单中，可将精确路径写入 `/etc/sysupgrade.conf`，同时离线备份。备份条目应限于配置和必要的数据文件，排除旧包数据库、二进制、内核模块及整个 overlay。
- **软件包**。4.0.0 使用 APK。额外安装的软件需要以匹配固件和架构的 APK 重装，kmod 须匹配正在使用的固件构建。旧 opkg URL 和密钥不会转换为 APK 配置。默认使用中科大 OpenWrt 镜像，BoltWrt 自定义包随固件交付。 [软件源设置](../config/version.txt) · [公共配置](../config/common.txt)
- **磁盘与引导**。x86 继续使用原 BIOS 或 UEFI 模式。改动过分区的设备先备份整盘，因为分区映射不匹配时升级可能改写整盘。RAX 的普通升级沿用现有引导与分区布局。 [镜像选择和刷写步骤](../README.md#升级)
- **SQM**。升级保留 `use_mq`。同一个 `kmod-sched-cake` 提供 `cake` 和 `cake_mq`，是否启用多队列取决于脚本、设备队列及实际 qdisc。用 `tc -s qdisc show` 核对出口和 IFB 上实际使用的队列规则。

### 阿里云 DDNS

旧 `ddns-scripts_aliyun` 由官方 `ddns-scripts-aliyun` 替换，服务名仍是 `aliyun.com`。升级将 `home.example.com` 转成 `home@example.com`，将 `@.example.com` 转成 `@example.com`。带 `@` 的现有格式、凭据、启用状态及其他 provider 保留。

迁移沿用旧 provider 的首个点拆分规则。对于不符合该规则的域名，在 LuCI 核对实际主机记录与托管域。存在未提交的 DDNS 改动或不支持的旧输入时，脚本延期或报错，待修正后在后续启动重试。实际记录更新仍取决于域名格式、凭据权限和服务连通性。 [迁移脚本](../package/boltwrt-defaults/files/etc/uci-defaults/95-boltwrt-aliyun)

### OpenVPN 2.7.7

| 检查项 | 升级前核对 |
| --- | --- |
| 非 TLS 静态密钥模式 | 2.7 默认拒绝旧的 `secret` 模式。使用此模式的实例先迁移到 TLS。 |
| 压缩 | 2.7 移除了发送压缩，仍可接收解压。核对对端配置和允许的压缩方式。 |
| IPv4 TUN 服务端 | 未显式指定 `topology` 时默认改为 `subnet`，检查手工 `ifconfig`、地址分配和路由。 |
| TLS 与 DCO | 核对 `data-ciphers` 及回退。产品使用 `kmod-ovpn-backports`，DCO 是否启用取决于实际配置和连接。 |

[OpenVPN 2.7.7 上游变更](https://github.com/OpenVPN/openvpn/blob/v2.7.7/Changes.rst)

## 默认配置回归

在一次性 Ubuntu 构建容器中可用原生 UCI／JSHN 验证 LAN、NTP 和 DDNS 迁移。将源码只读挂载到 `/work`，将匹配两个 Makefile 中 `PKG_SOURCE_VERSION` 的归档以 `libubox.tar.gz`、`uci.tar.gz` 挂载到 `/sources`。以容器内 root 执行 `bash /work/package/boltwrt-defaults/tests/run-native.sh`。脚本会安装容器依赖并写入该容器的 `/etc/config`。 [测试入口](../package/boltwrt-defaults/tests/run-native.sh) · [libubox](../package/libs/libubox/Makefile) · [UCI](../package/system/uci/Makefile)

已实测的升级起点、镜像组合和验收边界见版本记录。 [4.0.0 发布与验收](releases/4.0.0.md)
