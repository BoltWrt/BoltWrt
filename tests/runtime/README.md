# 固件运行测试

使用已核验的发布镜像或同一固定输入构建的产物。分别记录完整磁盘启动、客体重启、APK 操作、LuCI 交互和客体内实际 `sysupgrade`。每份记录应标明固件和 feed 提交、配置及镜像摘要、实际引导方式、命令、退出码和覆盖范围。保留原始失败及后续验证记录。 [固件构建](../../docs/build.md) · [4.0.0 验收实例](../../docs/releases/4.0.0.md)

## 准备测试环境

- 用仓库的 Dockerfile 构建专用 QEMU 容器。需要已有 Ubuntu 构建镜像中的原生 OpenWrt 工具时，通过 `--build-arg BASE_IMAGE=...` 选择镜像，并记录实际镜像 ID。受限 macOS 环境可将 `BUILDX_CONFIG` 指向项目内的任务目录。 [Dockerfile](Dockerfile)
- 将测试脚本和固件输入只读挂载。使用专用可写卷保存测试磁盘和证据，确保容器的非 root 用户有写权限。QEMU 使用 TCG，无需 `/dev/kvm`。默认转发端口共用，每个容器只运行一个用例。
- 管理端口只发布到宿主的 `127.0.0.1`。在容器内从脚本目录运行一次 `python3 tcp_forward.py` 并保持运行，它将容器端口 2222／8080／8443 转至 QEMU 监听的回环端口 2223／8081／8444。这样可按原有 LAN 配置访问客体。

## 启动与检查 x86 完整磁盘

为每个用例指定原始镜像的完整 SHA256，并使用全新的 `--case` 与 `--evidence` 目录。启动器复制整盘，UEFI 用例另复制一份可写 OVMF VARS。x86 用例从镜像中的引导程序启动。

以下为 BIOS IMG 的启动示例。在容器内执行，`/tools` 为只读测试脚本目录，`/runtime` 为可写卷。将变量设为容器可读的镜像路径和事先核对的 SHA256。

```sh
python3 /tools/qemu_runtime.py launch \
  --source "$firmware_image" --sha256 "$firmware_sha256" --format raw \
  --case /runtime/cases/bios-img --evidence /runtime/evidence/bios-img
python3 /tools/qemu_runtime.py exec --case /runtime/cases/bios-img \
  --command 'ubus call system board' --public-input \
  --result /runtime/evidence/bios-img/board.json
```

- IMG 或 IMG.gz 使用 `--format raw`，VMDK 使用 `--format vmdk`。UEFI 须同时传入匹配的 `--efi-code` 和 `--efi-vars`。完整启动矩阵包括 BIOS／UEFI 各自的 IMG 和 VMDK，用例逐个记录。
- 带旧 fwtool 尾块的 gzip 镜像可传入 `--fwtool /path/to/fwtool`。工具核验原输入摘要，检查尾块格式与 CRC，并另存 gzip 后完整解压。`preparation.json` 记录工具及结果摘要。镜像来源认证仍以可信的发布摘要为准。
- `exec --command-file` 经串口运行实际 ash 命令并记录退出码或错误。`--public-input` 仅供不含凭据的公开命令，允许无 `stty` 的精简客体回显。登录密码通过 `--password-file` 交给登录提示。证据中使用受控测试数据。
- 观察 PID 1、版本、根文件系统、overlay、默认 LAN、LuCI 登录及实际 HTTPS 握手。将新装检查脚本传入 4.0.0 客体，用 `sh verify-fresh.sh x86/64` 或 `sh verify-fresh.sh armsr/armv8` 检查系统状态。使用客体内 `reboot` 验证重启，再核对 boot ID、配置和服务状态。结束测试时可用 `quit --case ...` 发起 QMP 关机。 [新装检查脚本](guest/verify-fresh.sh)

## APK 与 LuCI

- 在实际客体中记录 APK 架构、已安装数据库、`world`、软件源和公钥。使用正常签名索引和同次构建的兼容 APK 验证安装、查询、真正的版本升级与卸载。按名称、版本、架构匹配包身份，选择非关键测试包。
- LuCI 的单个 HTTP(S) 包 URL 先由后端下载成临时 APK，再走标准安装路径。测试受客体信任的独立签名 APK，以及未签名包、缺失 URL、空响应、自签名 HTTPS 和 URL 混合其他包参数。安装使用正常信任检查。失败时应保留数据库和 `world`，并检查临时目录已清理、共享锁 inode 保持。下载超时是连接／请求的空闲超时。
- 配置页面测试应实际读取、保存并重新读取结果。分别记录 UI 交互、UCI 持久化、生产配置生成、核心加载和真实网络通信。

## 从 3.1.2 实际升级

用核验过摘要的 v3.1.2 正式镜像启动旧系统，从冷关机的基准磁盘分别派生 HAProxy enabled 与 disabled 用例。夹具记录标准备份清单、归档、服务状态标记、受控证书与脚本，随后由测试者在旧客体执行真实 `sysupgrade` 并连续保存串口输出。 [v3.1.2 发布页](https://github.com/BoltWrt/BoltWrt/releases/tag/v3.1.2) · [客体准备与升级后验证](guest/README.md)

升级后以及第二次客体内重启后，分别核对配置、服务启用意图、实际 HTTPS 证书和包管理状态。将镜像校验、刷写输出和重启后验证分别记录。 [平台与升级兼容](../../docs/platform.md)

## ARM 测试范围

- **通用 ARM 完整系统**。另建 `armsr/armv8` 夹具，以 `--arch aarch64 --format raw --kernel ...` 使用通用内核和持久化 ext4 rootfs 启动 QEMU `virt`。 [ARM 夹具构建](../../config/testing/README.md)
- **RAX ARM 用户态**。使用匹配生产 FIT 中的 rootfs，在隔离的原生 ARM 或 qemu-user 环境核对目标 ELF、APK、模块及有界配置解析。此层使用宿主内核。
- **RAX 板级**。启动链、闪存布局、驱动、无线及实际刷写须在对应硬件核对。通用 ARM VM 与 RAX 用户态是独立的软件测试层。
