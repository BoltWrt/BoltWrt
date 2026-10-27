# ARM VM 辅助夹具

`armsr-armv8.txt` 只供 QEMU `virt` 运行验证，不是第三个产品目标。
它与 `version.txt`、`common.txt` 组合，保留所有共同功能及 RAX 中适用的
存储、Samba、wsdd2 选择。唯一共同配置覆盖为
`CONFIG_TARGET_ROOTFS_EXT4FS=n → y`，同时保留 SquashFS；ext4 用于持久化 VM。

排除的是 RAX/Filogic target 与四个 MT7981 bootloader；RAX 的 initramfs
形态及 128 MiB rootfs 大小替换为独立通用 ARM 镜像选择和 512 MiB rootfs。
脚本显式列出两类排除，RAX 片段增加其它选择时不会静默丢弃。
armsr 内核已有 PL011、virtio 块设备/网卡及 ext4 支持，无需修改产品内核。

在独立、固定相同源码及 feeds 的构建目录中执行，不使用产品工作目录：

```sh
python3 scripts/boltwrt-arm-fixture.py --root /work
```

首次运行要求 `.config` 不存在，或恰好等于请求片段；已解析且检查通过的
同一夹具可以重复运行。遇到另一目标或缺失共同要求的现有配置会拒绝覆盖。
脚本执行真实 `make defconfig`，并将日志、退出码、配置快照、输入摘要、
明确覆盖/排除项及实际解析配置写入 `logs/boltwrt/armsr-armv8-fixture/`。
它不会调用、扩展或授予产品 `boltwrt-config.py`/`boltwrt-verify.py` 通过结果。

使用原有正常构建流程及所需 ARM64 host Go bootstrap 覆盖完成构建后，
显式传入这次构建的 manifest：

```sh
python3 scripts/boltwrt-arm-fixture.py --root /work --check-only \
  --manifest bin/targets/armsr/armv8/<actual-image>.manifest
```

manifest 参数必须位于本构建树的 armsr/armv8 产物目录；脚本记录原文件摘要、
真实包版本清单，使用 Python AST 仅提取同树 `boltwrt-verify.py` 的
`SERVICE_PACKAGES` 字面常量，检查完整共同服务前端/后端组，并额外要求
LuCI base、Samba 页面/后端和 wsdd2，以及 Linux 6.12.112 和 APK TLS 后端。
不执行产品 checker 的代码、检查函数或 CLI；该策略源码摘要也纳入证据。
manifest 本身不含包架构；
`aarch64_generic` 来自真实解析配置，后续还须核对实际 APK metadata/ELF，
不得混装 RAX `aarch64_cortex-a53` APK 或其它内核的 kmod。
这里只生成配置/清单证据，不宣称构建成功、运行通过或 RAX 板级验收。

保留 RAX 卷不变。可复用同 host/源码的 `dl`（含 `dl/cargo`）及 host 工具，
但不要复制其 target 库/APK/kmod 或把 cortex-a53 toolchain 目录改名。
Rust host 工具实际可能安装在 RAX target 下的 `host/` 子树；复用前核对
锁定 recipe、实际 ELF/链接和安装 stamp，不能仅复制 hostpkg stamp 就认为齐备。
构建/VM 的命令、CLI 覆盖、退出码、产物摘要和运行证据仍须由执行者单独记录。
