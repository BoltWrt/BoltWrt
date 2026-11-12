# 4.0.1 候选固件验收记录

本记录对应 2026-11-12 的本地 R3 候选。x86-64 与 RAX3000M / Me 均已完成
下述 Docker 验收。当前没有公开发布此候选版本。

## 构建输入与版本

软件版本快照核对日期为 2026-11-11。实际固件源码为
`d8cf60a9a7bce8dea0a8ce5ef12704d3563c3599`，Packages 为
`cbcdb6f0346ac0c151e8904956c1b1bf85dd251f`，source-lock SHA256 为
`61739252f66e9a3275fb0eb45cbfef5dd83ce825050a6c8a3b5e88d904d334a5`。
后续主仓提交修复交付校验和及发布文件范围、补充验收工具与文档，没有改变
此候选的固件内容。
交付工具自身的 SHA256 另写入每个平台的 `-build.json`。

| 组件 | 候选版本 |
| --- | --- |
| OpenWrt / Linux | 25.12.6 / 6.12.112 |
| GCC / binutils | 14.3.0 / 2.44 |
| Go 编译器 | 1.27.4，所有选中的 Go 应用统一使用 |
| PassWall2 | 26.11.11-r3，含 BoltWrt 兼容修复 |
| Xray | 26.11.9-r1 |
| Sing-Box | 1.14.4-r1 |
| 独立 Hysteria2 | 2.13.0-r1 |
| SS-libev / SSR-libev | 3.3.6-r1 / 2.5.6-r13 |
| Argon | 3.1.3-r20261106 |
| SmartDNS / LuCI | 48.4-r1 / 1.2026.48.3-r1 |
| frpc | 0.66.0-r1 |

Go 版本检查读取最终 rootfs 中 frpc、geoview、hysteria、sing-box、
v2ray-plugin、xray 六个 ELF 的真实构建信息。ARM64 构建主机使用的外部
Go 1.26.10 仅为编译 Go 1.27.4 的引导种子。

## 已修复的升级和交付缺口

- PassWall2 旧服务端配置转换为新版账户结构，保留原 section、协议、认证、
  证书路径和启停状态；二次启动不重复转换。转换前两个原始配置以 root 专有
  权限备份，回退时配合原版 4.0.0 备份恢复。
- 迁移旧客户端加密字段、WebSocket early-data、Xray RAW/TLS 指纹字段；
  处理缺省 TCP disguise、二进制 Hysteria 认证及含 NUL 的 SHA256 数据。
  Sing-Box 1.14 不支持的证书指纹配置明确报错，支持通过 PEM 证书建立信任。
- 移除旧 `disable` 端口占位值，转换 ACL 模式和来源列表，保留全局关闭、
  动态继承端口、指定节点共享或独立运行的行为。保存新版 ACL 页面后使用
  页面所示的新设置。
- 保留独立 Hysteria2、SS-libev、NaiveProxy、TUIC 后端。PassWall2 界面随
  固件更新，核心更新检查仅选择正式版本，并使用 CA 校验和 POST/CSRF 保护。
- 交付与固件清单精确匹配的预装 APK，逐个签名并使用实际固件公钥严格验证；
  在生成 APK 归档和构建记录后刷新两份校验清单，修复增量构建中的旧校验值。
  标准清单仅引用实际发布的文件，避免引用未随 RAX 固件包发布的其他
  MediaTek 平台通用 RAM 引导文件；这些文件仍保留在 SDK。

## x86-64：已通过

完整源码构建、357 项产品选择、六种镜像结构、软件版本和 Go 构建信息检查
均通过。341 个预装 APK 与 manifest 精确匹配并逐个通过严格签名验证。
产品清单的 11 个条目与 OpenWrt 标准清单的 16 个条目均校验成功。
保留了 91 项完整源码检查通过、无跳过的记录；此后新增 RAX 发布集合回归，
交付专项六项测试均通过。

| Docker 内 QEMU 场景 | 结果 | 独立启动次数 |
| --- | --- | --- |
| BIOS 首装及真实重启 | 通过 | 2 |
| UEFI 首装及真实重启 | 通过 | 2 |
| 官方 4.0.0 BIOS → 4.0.1，原服务启用，重启，再回退原版与原备份 | 通过 | 4 |
| 官方 4.0.0 UEFI → 4.0.1，原服务禁用，真实重启 | 通过 | 3 |

升级使用普通保留配置 `sysupgrade`，不使用 `-F`。升级和重启后均核对全部
341 个已安装软件包、管理地址、密码、SSH 身份、TLS 证书、原始迁移备份、
17 份旧服务端配置及三类 ACL。真实 ACL 生成器的四种策略均通过。
13 份生成配置由实际 Xray / Sing-Box 核心检查；七条本地代理流量和错误
证书 pin 拒绝通过。完整客户端启动成功安装 nftables 规则，HTTP/DNS 流量、
实际 dnsmasq reload 及进程稳定性检查通过。APK 篡改被拒绝，原生卸载后
重装恢复原包清单和信任公钥。BIOS 回退恢复原版配置字节、schema 和包清单。

Docker 内 Chromium 完成真实 root 登录及九个 Argon / PassWall2 页面渲染，
未发现未捕获 JavaScript 异常或本地资源加载失败。已查看概览和独立
Hysteria2 页面截图；没有进行主题外观定制。浏览器及编译依赖均在 Docker
内，宿主机未安装依赖或修改证书信任。

原始结果保存在项目私有目录
`.theme-preview/software-refresh-20261111/runtime-401/`。
`x86-r3-acceptance.json` 汇总四个场景、启动 ID、工具 SHA256 和浏览器结果；
各场景原始结果与失败诊断记录保留。执行方法见
[运行验收指南](../../tests/runtime/README.md)。私有测试证书、密码和备份不进入固件包。

本地完整候选归档为 `BoltWrt_x86-64_v4.0.1-candidate-r3.tar.gz`，
669056662 字节，SHA256：

```text
7ba5ee183c325d3177f2fd4a5185b80f4b44ffbb29b36349e267df517237fc53
```

归档的 17 个公开文件已与核验后的导出逐项比对；包含固件、签名 APK、
构建信息和两份校验清单，不含测试配置或私钥。

## RAX3000M / Me：已通过 Docker 成品验收

完整源码 world、391 项产品选择、12 个镜像及引导资产、FIT 格式与容量预算、
NAND/eMMC 和 DDR3/DDR4 四种组合的引导文件检查均通过。实际 rootfs 中
六个 Go 程序统一为 Go 1.27.4 / linux / arm64 / v8.0；软件版本与锁定记录
一致。358 个预装 APK 逐个签名并严格验证，标准清单的 21 个条目和产品清单
的 16 个条目均校验成功。

原版 4.0.0 ARM 用户态中的 `sysupgrade -T` 分别接受官方 4.0.0 FIT 和
候选 4.0.1 FIT；两次检查均拒绝故意设置的错误设备名。候选元数据保留
`cmcc,rax3000m`、compatibility version 1.0，无需 `-F`。

实际 ARM64 rootfs 导入 Docker 后，使用原版 4.0.0 ARM 用户态生成的同一份
旧配置夹具完成原生验收：真实 ubusd/rpcd/UCI、迁移备份权限与原始字节、
幂等性、17 个旧服务端、三类旧 ACL 与四种实际策略均通过。17 份生成配置中
13 份由实际 Xray / Sing-Box 检查；七条本地代理流量、错误证书 pin 拒绝、
严格 APK 验证、篡改拒绝、卸载和重装均通过。APK 事务前后全部 358 个包
与成品 manifest 精确一致。95 个 kmod 包的 kernel ABI 依赖，以及 135 个
模块的 ARM64 ELF / vermagic 元数据检查通过。

原生测试容器使用 Docker 自带 bridge、2 CPU / 1 GiB、仅 `NET_RAW` 能力，
没有发布宿主端口。此前 loopback-only 环境缺省网卡、全能力剥离导致出口
绑定被拒绝的失败均保留；最终在全新容器中通过，没有修改固件生成的配置
来绕过这些环境条件。证据汇总为私有目录中的
`runtime-401/arm-r3-acceptance.json`，原生进程 exit 0，结束前 sync 与 stop
均为 0。Rust 构建资源配置与两次早期构建失败/中断记录也保留。

本地完整候选归档为 `BoltWrt_cmcc-rax3000m_v4.0.1-candidate-r3.tar.gz`，
374191542 字节，SHA256：

```text
7fe9b284171be9ca9e2e5e04b285b67a1d237452823cb4298a0283af545f2abb
```

归档 22 个公开文件逐项匹配已核验导出；外层 `SHA256SUMS` 同时保留 x86
和 RAX 两个归档条目。实际 RAX rootfs 的 SHA256 为
`d224832cd7596277b84153b010d9b1357eafca8564a594240cef72b083f962e0`，
sysupgrade FIT 的 SHA256 为
`d19fb2ee9cfe73df70a08eac9658d4dca1782aeac83bb0509dd3e36124d4a5c5`。

## 验证范围

本记录的运行验收使用 Docker、x86 QEMU 和本地代理对端。ARM 用户态容器
不运行 RAX 内核。物理刷写、交换机/无线、外部 WAN 或实际服务商对端不在
本次 Docker 验收范围内。
