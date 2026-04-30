# BoltWrt

 -----------------------------------------------------
     __________       .__   __   __      __          __   
     \______   \ ____ |  |_/  |_/  \    /  \________/  |_ 
      |    |  _//  _ \|  |\   __\   \/\/   /\_  __ \   __\
      |    |   (  <_> )  |_|  |  \        /  |  | \/|  |  
      |______  /\____/|____/__|   \__/\  /   |__|   |__|  
             \/                        \/                 
                                      
 -----------------------------------------------------

## About BoltWrt - 关于BoltWrt

BoltWrt是一款嵌入式路由器系统,它在OpenWrt的基础上,针对中国的用户需求和网络环境,添加了很多高级功能和特性.它的目标是打造一个高效,稳定,安全,易用的路由器系统.

从2021年元月开始,BoltWrt正式对外公布,BoltWrt团队在积极的开发和维护着它,同时BoltWrt是社区合作的成果,欢迎大家参与其中,贡献自己的力量.

## Features - 特性

- 继承OpenWrt-19.07.7的所有特性,兼容OpenWrt的配置;
- 友好的 Material Design 风格界面
- 适用于中国网络的配置调整与功能实现
- 实时与多维度的运行状态监视
- 加强\*的安全防护
- Nessus主机脆弱性扫描无任何风险项
- 详细的说明文档和升级日志

## Configuration recommendation - 推荐配置

- CPU:支持64位指令集的英特尔处理器
- RAM:不小于128MB
- ROM:容量不小于1.1GB\*\*的HDD或SSD
- Ethernet interface:至少拥有1个板载以太网接口

## 标准部署流程

### 裸机

1. 使用IMG写盘工具将固件写入硬盘
2. 将终端设备接入第一个网口并通过因特网浏览器访问192.168.1.1进入管理界面
3. 根据需求配置系统,并开始使用!

### 虚拟化设备

以VMware产品为例:

1. 建立虚拟主机并分配至少1.1GB的硬盘容量
2. 使用img写盘工具将固件写入硬盘中
3. 根据需求配置系统,并开始使用!

注:不可使用img转虚拟化磁盘的转换工具,否则分区将会损坏导致无法正常使用.<br>

## 编译流程

### 交叉编译硬件配置要求

1. 更多的核心有助于加快编译速度
2. 不低于4GB的内存,从性价比的角度考虑也并非越高越好
3. 不少于60GB的可用硬盘空间

### 交叉编译指引

截至目前,编译BoltWrt的方式与编译OpenWrt相同,除此之外,我们建议您使用`Ubuntu 20.04`系统,在非root用户下进行:<br>
1. 安装编译必须的依赖组件
```bash
sudo apt-get install $(curl -fsSL git.io/OpenWrtPrerequisites-Ubuntu_2004)
```
2. 获取源码,并做编译前的必要工作
```bash
git clone --branch v1.1.0 https://github.com/Morton-L/BoltWrt.git && chmod -Rf 777 BoltWrt/
cd BoltWrt/ && ./scripts/feeds update -a && chmod -Rf 777 feeds && ./scripts/feeds install -a
```
3. 使用`make menuconfig`命令进入模拟图形化界面定义平台,架构等固件的基础配置
4. 下载编译进系统的各类依赖和组件
```bash
make download -j8
```
5. 开始编译(多线程)
```bash
make -j$(nproc)
```
或者您也可以指定参数j的数值为1作最稳妥的单线程编译,他将会延长您的编译时间

### 获取固件

您需要大约1个小时到3个小时不等的时间来等待固件编译完成,这取决您的网络环境与计算机性能



## 固件说明

您可以通过访问 [固件](https://github.com/Morton-L/BoltWrt/releases) 页面来获取正式版固件,并根据`标准部署流程`的指引进行部署操作<br>
出于稳定性,安全性角度考虑,目前仅提供且推荐使用squashfs文件系统的固件,因为当系统遇到灾难性故障时<br>
您可能需要通过"恢复出厂设置"功能来快速恢复设备状态,这时我们会利用到squashfs文件系统的特性,您可以<br>
放心的是squashfs文件系统并不会影响正常的软件安装,更新或卸载,它犹如您的IPhone或Android手机一样提<br>
供可靠保障<br>
当然,您也可以根据`编译流程`的指引,自行编译其他文件系统的固件,但设备的可靠性\*\*\*有可能会降低

## Changelog - 变更记录

### 版本策略

- 主版本号: 做了不兼容的 API 修改，
- 次版本号: 做了向下兼容的功能性新增，
- 修订号  : 做了向下兼容的问题修正。

#### BoltWrt-1.2.5

- 删除无用芯片支持
- tcpdump:
	1. Fix CVE-2018-16301
	2. libpcap: Remove http://www.us.tcpdump.org mirror
- wolfssl:
	1. bump to 5.2.0
	2. fix API breakage of SSL_get_verify_result
	3. fix compilation with /dev/crypto
- kernel:
	1. bump 4.14 to 4.14.275
	2. generic: reorder kernel config options
	3. generic: add missing symbol for arm64 spectre mitigation
- build: store SOURCE_DATE_EPOCH in JSON info files
- mbedtls: Update to version 2.16.12
- hostapd: Apply SAE/EAP-pwd side-channel attack update 2
- ubus: backport fixes for UAF and other issues
- imagebuilder: fix partition signature
- base-files: call "sync" after initial setup
- openssl: bump to 1.1.1n
- zlib: backport security fix for a reproducible crash in compressor
- patchelf: backport fix for rpath endianness
- imagebuilder: fix broken image generation with external targets
- mac80211: Update to version 4.19.237-1


#### BoltWrt-1.2.4

- openssl: 
	1. 在配置中使用 --cross-compile-prefix
	2. 升级至1.1.1m
- 内核升级: 升级至4.14.261
- uboot:
	1. 修复使用GCC-10作为主机编译器编译
	2. layerscape和zynq修复使用dtc作为主机编译器编译
	3. 修复安装系统 libmd 时的 sha1.h 标头冲突
	4. fix hanging lzma kernel uncompression
- 问题修复: sdk:修复缺少的包含目录
- 问题修复: ar71xx: mikrotik: rb91x: fix 10M ethernet link speed
- 模块更新: mac80211: 更新至 backports-4.19.207-1
- 模块更新: tools/m4: 更新至 1.4.19
- 模块更新: wireless-regdb: 更新至 version 2021.08.28
- 模块更新: 更新数十个模块为最新版本不再一一列举
- 国际化: 更新多个组件/模块/功能的po语言文件
- 增加驱动: 增加VMware半虚拟化网卡支持
- 新增功能: 支持阿里云盘WebDAV


#### BoltWrt-1.2.3

- 增加驱动: 增加RealTek RTL-8139和RTL-8139C+网卡驱动
- 增加驱动: 增加博通BCM5706/5708/5709/5716网卡驱动
- 增加驱动: 增加Intel(R) 82576网卡驱动
- 增加驱动: 增加Intel(R) 82598/82599万兆网卡驱动
- 增加驱动: 增加RealTek RTL-8169网卡驱动
- 增加模块: SmartDNS
- 增加模块: OpenConnect VPN server
- 增加功能: DDNS支持阿里云域名
- 功能调整: 将IP/MAC绑定整合进DHCP菜单

#### BoltWrt-1.2.2

- 内核升级:升级至4.14.236
- 模块更新: mbedtls: update to 2.16.10
- 模块更新: openssl: bump to 1.1.1k
- 模块更新: mac80211: Update to backports version 4.19.193-test1
- 模块更新: openvpn: update to 2.4.11
- 模块更新: ramips: backport unlocked mdiobus accessors
- 模块更新: passwall
- 模块更新: ssr-plus
- 模块更新: Golang升级至1.16.6
- 模块更新: https-dns-proxy: update to 2021-06-03-1
- 模块更新: netdata: update to version 1.30.1
- 组件更新: syslog-ng: update to 3.32.1
- 组件更新: net/mosquitto: Update to 1.6.15
- 组件更新: nano: update to 5.8
- 组件更新: nextdns: Update to version 1.34.2
- 组件更新: snort3: Backport stable version from 21.02
- 组件更新: 升级各类组件和依赖数项,同时清理过期过时和无用的组件
- 功能优化: reduce number of files passed to ipk-remove
- 功能优化: generic: platform/mikrotik: release mtd device after use
- 功能调整: call ipkg-remove using xargs if #args>=512
- 功能调整: czmq: disable nss
- 功能调整: python-dateutil: 
	1. disable setuptools-scm for build
	2. pin setuptools-scm version to 5.0.2
- 功能调整: mwlwifi: add PKG_FLAGS:=nonshared
- 功能调整: Revert "python-dateutil: disable setuptools-scm for build"
- 功能调整: ddns-scripts: standardize required params declaration
- 功能调整: prereq-build: test for perl's Data::Dumper
- 功能调整: openwrt-keyring: 
	1. add OpenWrt 21.02 GPG/usign keys
	2. Only copy sign key for 19.07 and 21.02
- 功能调整: mac80211: distance config: allow "auto" as a value
- 功能调整: base-files: 
	1. fix zoneinfo support 
	2. fix /tmp/TZ when zoneinfo not installed
- 功能调整: banip: remove logd dependency
- 功能调整: xr_usb_serial_common: add PKG_MIRROR_HASH
- 问题修复: build: package-ipkg: avoid calling wildcard twice
- 问题修复: bundle-libraries.sh: fix broken SDK compiler
- 问题修复: ppp/pppoe-discovery: fix -W option
- 问题修复: ramips: mt7530 swconfig: fix race condition in register access
- 问题修复: dropbear: Fix CVE-2020-36254
- 问题修复: tplink-safeloader: fix C7v5 factory flashing from vendor fw > v1.1.x
- 问题修复: tools/mklibs: Fix compile with GCC 11
- 问题修复: ubox: fix init script validation of log_ip option
- 问题修复: ath10k-ct: add security fixes
- 问题修复: 修复一个编译报错信息
- 问题修复: https-dns-proxy: 
	1. bugfixes for dhcp server backup
	2. bugfix: fallback to HTTP/1 by default
- 问题修复: xr_usb_serial_common: fix build

#### BoltWrt-1.2.1

- 模块更新:wolfssl: 更新至 v4.7.0-stable
- 模块更新:ninja: 更新至 1.10.2并支持CMake编译
- 模块更新:nano: 更新至 5.6.1
- 模块更新:libpam: 更新至 1.5.1
- 模块更新:nextdns: 更新至 1.11.0
- 问题修复:hostapd: P2P: Fix a corner case in peer addition based on PD Request
- 问题修复:build: fix checks for GCC11
- 问题修复:修复几个功能的ui显示问题
- 功能调整:清华大学软件源镜像站版本切换为19.07.7
- 功能优化:vpn-policy-routing: better processing of custom user files


#### BoltWrt-1.2.0

- 添加SCSI设备支持
- 添加USB 1.0,1.1,2.0,3.0的支持
- 添加USB大容量存储支持
- 添加设备挂载支持
- 添加对exfat,ext4,ntfs,FAT16/32文件系统支持
- 添加驱动:ntfs文件系统读写驱动
- 新增功能:e2fsprogs工具
- 新增功能:usbutils工具
- 新增功能:mvebu: omnia: make initramfs image usable out of the box
- 新增功能:SSH
- 国际化:添加西欧常用字符`ISO 8859-1`编码,`utf8`编码,`ASCII`(CP437)编码,中国`GBK`(CP936)编码支持
- 国际化:treewide: i18n backport and 同步翻译
- 模块更新:mt76: 更新到最新版本
- 模块更新:wolfssl: 更新到 v4.6.0-stable
- 模块更新:opkg: update to latest git HEAD of branch openwrt-19.07
- 模块更新:openssl: 升级至 1.1.1j
- 模块更新:bcm63xx: sprom: override the PCI device ID
- 模块更新:ksmbd-tools: 升级至 3.3.4
- 模块更新:ksmbd: 升级至 3.3.4
- 模块更新:mosquitto: 升级至 1.6.13
- 模块更新:bind: 升级至 9.16.12
- 模块更新:zerotier: 升级至 1.6.4
- 模块更新:python3: 升级至 3.7.10, refresh patches
- 模块更新:netdata: 升级至 1.29.2
- 模块更新:vpn-policy-routing: 升级至 0.3
- 模块更新:openvswitch: 升级至 2.11.6 (security fix)
- 模块更新:python-paho-mqtt: 升级至 1.5.0
- 模块更新:htop: 升级至 3.0.5-1
- 模块更新:knot: 升级至 3.0.4
- 模块更新:adblock: update blocklist sources
- 模块更新:libedit: 升级至 20210216-3.1
- 模块更新:nano: 升级至 5.6
- 模块更新:unbound: 升级至 1.13.1
- 模块更新:python-maho-mqtt: 升级至 1.5.1
- 模块更新:vpn-policy-routing: 升级至 0.3.2-18
- 模块更新:vpn-policy-routing: 
    1. custom user scripts improvements
	2. update user netflix file
- 问题修复:wolfssl: 
    1. Fix hostapd build with wolfssl 4.6.0
	2. 修复漏洞:CVE-2021-3336
- 问题修复:hostapd: fix P2P group information processing vulnerability
- 问题修复:hostapd: backport ignoring 4addr mode enabling error
- 问题修复:bcm63xx: R5010UNv2: fix flash partitions for 16MB flash
- 问题修复:ath79: fix USB power GPIO for TP-Link TL-WR810N v1
- 问题修复:luci-base: 
    1. luci.js: 修复 sortedKeys() ordering
	2. ui.js: resolve parent ul early in UIDropdown.toggleItem()
- 问题修复:simple-adblock: bugfix: ListValue的默认值
- 问题修复:ddns: fix multiple authenticated RCEs
- 问题修复:防火墙: 
    1. properly handle custom multi IP/MAC input
	2. fix creating multiple networks from zone network selector
	3. zones.js: fix HTML display in ct helper selection
- 问题修复:rpcd-mod-luci: Fix parsing of DUID-LLT's in duid2ea
- 问题修复:keepalived: 
    1. 添加脚本安全参数以修复警告
    2. 修复配置错误
- 问题修复:isc-dhcp: seeing crashes when attempting to update dynamic dns
- 问题修复:vpn-policy-routing: bugfix: netflix user file missing redirect
- 漏洞修复:screen: backport fix for CVE-2021-26937
- 内核升级: 升级为4.14.222
- 系统调整:wolfssl: 启用 HAVE_SECRET_CALLBACK
- 系统调整:ramips: 
    1. remove factory image for TP-Link Archer C2 v1
    2. remove factory image for TP-Link Archer C20 v1
	3. mark toggle input on EX6150 as a switch
	4. ethernet: 禁用 TSO支持以提高稳定性
- 系统调整:mac80211: Remove 357-mac80211-optimize-skb-resizing.patch
- 系统调整:lantiq: fritz7320: 启用 USB 电源
- 功能调整:https-dns-proxy: 
    1. 添加 nextdns.io 和 quad 101 接口
	2. add Force DNS, IDNet support, add missing class to buttons
- 功能调整:luci-base: 
    1. network.js: sort interface statuses by metric, then name
    2. validation.js: optionally support negative prefixes
	3. firewall.js: manage zomne networks as list
- 功能调整:防火墙: allow negative prefix lengths
- 功能调整:simple-adblock: 移除对jsonfilter和旧代码的依赖
- 功能调整:keepalived: set default run directory for pid file on build
- 功能调整:getdns: 
    1. fix compilation without deprecated OpenSSL APIs
    2. Fix TLS V1.3 Ciphersuites option in Stubby
	3. disable static linking of getdns utilities
- 功能调整:https-dns-proxy: support for force DNS/DNS hijacking
- 功能调整:ttyd: 强制启用登录身份验证
- 功能调整:ksmbd: remove kmod-crypto-arc4 dependency
- 功能调整:knot: 
    1. disable libnghttp2 autodetection
    2. disable embedded xdp

#### BoltWrt-1.1.0

- 新增功能:frpc端口映射工具
- 新增功能:ZeroTier虚拟内网工具
- 新增模块:Samba实现SMB协议
- 问题修复:imagebuilder: pass IB=1 on checking requirements
- 内核升级: 由 4.14 升级为 4.14.217
- 问题修复:wireguard: Fix compile with kernel 4.14.217
- 问题修复:odhcp6c: fix IPv6 routing loop on point-to-point links
- 问题修复:netifd: fix IPv6 routing loop on point-to-point links
- 问题修复:dnsmasq: backport fixes
- 模块更新:openwisp-config: 更新至 0.5.0
- 问题修复:https-dns-proxy: CPU使用率问题
- 模块更新:youtube-dl: 更新至 2021.1.16
- 模块更新:msmtp: 更新至 1.8.14
- 问题修复:php7: Fix prepare target incorrectly referencing 'configure.in' instead of 'configure.ac'
- 模块更新:nextdns: 更新至 1.10.1
- 问题修复:samba4: 
	1. 修复可能的漏洞
	2. sanetize all external template/config inputs
	3. 修复一些 shellcheck 警告
- 漏洞修复:sudo: backport patches for CVE-2021-3156
- 配置调整:luci-app-sqm: remove orphaned ACL file
- 国际化:treewide: Sync and backport translations from master
- 数据更新:时区数据: 更新至2021a

#### BoltWrt-1.0.0

- 新增功能:支持通过HTTPS协议访问后台
- 新增功能:NetData实时性能监控
- 新增功能:广告过滤
- 新增功能:防火墙
- 新增功能:封禁IP
- 新增功能:DDNS服务
- 新增功能:访问内容过滤
- 新增功能:DNS Over HTTPS代理
- 新增功能:NTP授时客户端
- 新增功能:Let's Encrypt SSL证书管理
- 新增功能:OpenVPN
- 新增功能:UPnp(端口自动转发)
- 新增功能:断网监控与自动重启
- 新增功能:流量监控
- 新增功能:流量智能队列管理
- 新增功能:QOS流量控制
- 新增功能:opkg包管理
- 新增功能:带宽监视器
- 新增功能:上网时间控制
- 新增功能:IP/MAC 绑定
- 新增功能:iperf3网络速度测试工具
- 新增功能:PassWall
- 新增功能:SSR-Plus
- 新增功能:进程查看器
- 新增功能:防火墙
- 新增功能:WOL网络唤醒
- 新增模块:iperf3网络测速工具
- 新增模块:UPX压缩
- 系统调整:对时服务器调整为ntp.org,微软,阿里云和谷歌
- 系统调整:引导等待时间缩短至3秒减少等待时间
- 系统调整:软件源切换为清华大学镜像站
- 内核调整:扩容内核分区大小至128MB,根分区大小扩容至1GB
- 依赖升级:Golang升级至1.15.7版本
- 新增主题:argon
- 新增主题:Material






## Acknowledgments - 致谢

- [OpenWrt](https://github.com/openwrt/openwrt)
- [argon](https://github.com/jerrykuku/luci-theme-argon)
- [清华大学开源软件镜像站](https://mirrors.tuna.tsinghua.edu.cn)
- [UPX](https://upx.github.io)
- [frp](https://github.com/fatedier/frp)
- [ZeroTier](https://www.zerotier.com/)
- [Samba](https://www.samba.org/)

## 标记

- \*:相较于原始OpenWrt固件
- \*\*:1024MB=1.0GB
- \*\*\*:固件的更改将会永久性生效,包括非法的修改,所以您将无法真正使用"恢复出厂设置"功能

## License

[GPL](LICENSE)
