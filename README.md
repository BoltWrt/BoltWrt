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

BoltWrt是由我发起的路由器系统,它在OpenWrt的基础上,针对中国用户需求,添加了很多高级功能和特性.它的目标是打造一个高效,稳定,安全,易用的路由器系统.

从2021年元月开始,BoltWrt正式对外公布,BoltWrt团队在积极的开发和维护着它,同时BoltWrt是社区合作的成果,欢迎大家参与其中,贡献自己的力量.

## Features - 特性

- 继承OpenWrt-19.07.6的所有特性,兼容OpenWrt的配置;
- 友好的 Material Design 风格界面
- 适用于中国网络的配置调整与功能实现
- 实时与多维度的运行状态监视
- 加强\*的安全防护
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

1. 使用img转换工具将固件转换为vmdk格式
2. 将转换后的固件使用VMware Workstation程序作为磁盘文件加载至虚拟机或使用ESXi的网页控制面板上传并作为虚拟机的磁盘使用
3. 配置网络接口,默认情况下第一个网络接口为LAN,第二个网络接口为WAN,并通过因特网浏览器访问192.168.1.1进入管理界面
4. 根据需求配置系统,并开始使用!

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

- 主版本号：做了不兼容的 API 修改，
- 次版本号：做了向下兼容的功能性新增，
- 修订号  ：做了向下兼容的问题修正。

#### BoltWrt-1.1.0

- 新增功能:frpc端口映射工具
- 新增功能:ZeroTier虚拟内网工具
- 新增模块:SMBA实现SMB协议
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

## 标记

- \*:相较于原始OpenWrt固件
- \*\*:1024MB=1.0GB
- \*\*\*:固件的更改将会永久性生效,包括非法的修改,所以您将无法真正使用"恢复出厂设置"功能

## License

[GPL](LICENSE)
