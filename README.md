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

BoltWrt是一款路由器系统,它在OpenWrt的基础上,针对中国的用户需求和网络环境,添加了很多高级功能和特性.它的目标是打造一个高效、稳定、安全、易用的路由器系统.

从2021年元月开始,BoltWrt正式对外公布,BoltWrt团队在积极的开发和维护着它,同时BoltWrt是社区合作的成果,欢迎大家参与其中,贡献自己的力量.

## Features - 特性

- 继承 OpenWrt-24.10 的所有特性,兼容 OpenWrt 的配置;
- 友好的 Material Design 风格界面
- 适用于中国网络的配置调整与功能实现
- 实时与多维度的运行状态监视
- 加强的安全防护
- Nessus 主机脆弱性扫描无任何风险项

## Configuration recommendation - 推荐配置

- CPU: 支持 64 位指令集的处理器
- RAM: 不小于 256 MB
- ROM: 不小于 256 MB
- Ethernet interface: 至少拥有 1 个板载以太网接口

## 标准部署流程

### 裸机

1. 使用IMG写盘工具将固件写入硬盘
2. 将终端设备接入第一个网口并通过因特网浏览器访问 10.126.0.1 进入管理界面
3. 根据需求配置系统,并开始使用!

### 虚拟化设备

以VMware产品为例:

1. 建立虚拟主机并使用 VMDK 作虚拟硬盘
2. 调整 /etc/config/network 文件正确网卡绑定至正确接口（如 WAN 绑定至 eth0）
3. 根据需求配置系统,并开始使用!

## Acknowledgments - 致谢

- [OpenWrt](https://github.com/openwrt/openwrt)
- [argon](https://github.com/jerrykuku/luci-theme-argon)
- [清华大学开源软件镜像站](https://mirrors.tuna.tsinghua.edu.cn)

## License

[GPL](LICENSE)
