# 主题预览环境

使用现成 BoltWrt 4.0.0 x86-64 固件，在专用 Docker 容器内通过 QEMU
启动完整系统。宿主只需要已有 Docker，不安装 QEMU、Python、Node 或其他依赖，
不配置宿主路由、网桥或证书信任。Docker 构建元数据也保存在项目目录中。

## 初次准备

在 BoltWrt Git 仓库根目录执行：

```sh
sh tools/theme-preview/preview.sh build
sh tools/theme-preview/preview.sh prepare /完整路径/BoltWrt_x86-64_v4.0.0.tar.gz
sh tools/theme-preview/preview.sh up
sh tools/theme-preview/preview.sh logs
```

固件自行从 [BoltWrt v4.0.0](https://github.com/BoltWrt/BoltWrt/releases/tag/v4.0.0)
下载。工具不会自动下载或重新编译固件。导入会核对官方资产的完整大小及 SHA256：

```text
570335990 bytes
9cb99f69be67bc3f517d4c51fecfc547a4f142298d58a748ec7814f6490432b3
```

只提取 BIOS combined VMDK，原始下载文件保持不变；启动时新建 QCOW2 写入层，
后续启动复用这一写入层，不反复解压或复制固件。

日志显示 `Preview ready` 后访问 `https://127.0.0.1:18443/`。首次访问需接受
该预览固件的自签名证书。用户名 `root`，随机密码记录于
`.theme-preview/credentials.json`，它是本预览环境的初始密码。

端口可以在启动前通过 `BOLTWRT_PREVIEW_PORT=其他端口` 指定。

## 修改与同步

默认源目录是同一工作区已有的 `../Packages/luci/theme/luci-theme-argon`。
该目录以只读方式挂载到容器。工具监听 `htdocs/` 和 `ucode/`，文件保存稳定后
自动同步到虚拟机；也可以手动同步：

```sh
sh tools/theme-preview/preview.sh sync
```

同步完成后刷新浏览器。模板中的静态资源 URL 带本次内容标识，避免旧 CSS 缓存；
uWSGI 仅在发生变化的同步后重启。源码中的 LESS 需要先生成对应 CSS，工具不会
自动编译 LESS。包依赖、RPC 服务、UCI 默认脚本和语言包安装也不属于文件同步。
跨 Argon 大版本测试应先在预览虚拟机中安装匹配的完整主题包及依赖。

## 日常操作

```sh
sh tools/theme-preview/preview.sh status
sh tools/theme-preview/preview.sh logs
sh tools/theme-preview/preview.sh stop
sh tools/theme-preview/preview.sh up
```

停止会请求虚拟机关机，移除临时容器并保留磁盘、配置和同步状态。固件、运行状态、
凭据及日志均位于 Git 忽略的 `.theme-preview/`。原始 VMDK 作为只读后备盘；
虚拟机修改只进入写入层。容器只发布宿主 `127.0.0.1` 的 HTTPS 端口，SSH 仅供
容器内部同步；QEMU 用户网络启用 `restrict=on`，不接入真实局域网，也不开放 WAN。

## 验证状态

已使用官方 4.0.0 固件验证完整系统启动、预览账号的 LuCI/ubus 认证、停止后恢复、
证书保留，以及修改 CSS 后页面资源地址和实际返回内容同步变化。临时样式已恢复，
当前主题仍为固件自带的 Argon 2.4.3。

Chrome 已完成登录、默认管理页显示，以及「修改 CSS → 自动同步 → 刷新后显示变化
→ 恢复原样式」的实际浏览器验证。验收记录位于 `.theme-preview/acceptance.json`，
基线、同步效果及恢复后的截图位于 `.theme-preview/screenshots/`。

首次访问的自签名证书提示仍需用户手动处理。工具不会自动绕过浏览器的安全警告，
也不会将证书安装进宿主信任存储。
