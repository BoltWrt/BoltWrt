DTS_DIR := $(DTS_DIR)/mediatek

define Image/Prepare
	# For UBI we want only one extra block
	rm -f $(KDIR)/ubi_mark
	echo -ne '\xde\xad\xc0\xde' > $(KDIR)/ubi_mark
endef

define Build/mt7981-bl2
	cat $(STAGING_DIR_IMAGE)/mt7981-$1-bl2.img >> $@
endef

define Build/mt7981-bl31-uboot
	cat $(STAGING_DIR_IMAGE)/mt7981_$1-u-boot.fip >> $@
endef

define Build/mt798x-gpt
	cp $@ $@.tmp 2>/dev/null || true
	ptgen -g -o $@.tmp -a 1 -l 1024 \
		$(if $(findstring sdmmc,$1), \
			-H \
			-t 0x83	-N bl2		-r	-p 4079k@17k \
		) \
			-t 0x83	-N ubootenv	-r	-p 512k@4M \
			-t 0x83	-N factory	-r	-p 2M@4608k \
			-t 0xef	-N fip		-r	-p 4M@6656k \
				-N recovery	-r	-p 32M@12M \
		$(if $(findstring sdmmc,$1), \
				-N install	-r	-p 20M@44M \
			-t 0x2e -N production		-p $(CONFIG_TARGET_ROOTFS_PARTSIZE)M@64M \
		) \
		$(if $(findstring emmc,$1), \
			-t 0x2e -N production		-p $(CONFIG_TARGET_ROOTFS_PARTSIZE)M@64M \
		)
	cat $@.tmp >> $@
	rm $@.tmp
endef

metadata_gl_json = \
	'{ $(if $(IMAGE_METADATA),$(IMAGE_METADATA)$(comma)) \
		"metadata_version": "1.1", \
		"compat_version": "$(call json_quote,$(compat_version))", \
		$(if $(DEVICE_COMPAT_MESSAGE),"compat_message": "$(call json_quote,$(DEVICE_COMPAT_MESSAGE))"$(comma)) \
		$(if $(filter-out 1.0,$(compat_version)),"new_supported_devices": \
			[$(call metadata_devices,$(SUPPORTED_DEVICES))]$(comma) \
			"supported_devices": ["$(call json_quote,$(legacy_supported_message))"]$(comma)) \
		$(if $(filter 1.0,$(compat_version)),"supported_devices":[$(call metadata_devices,$(SUPPORTED_DEVICES))]$(comma)) \
		"version": { \
			"release": "$(call json_quote,$(VERSION_NUMBER))", \
			"date": "$(shell TZ='Asia/Chongqing' date '+%Y%m%d%H%M%S')", \
			"dist": "$(call json_quote,$(VERSION_DIST))", \
			"version": "$(call json_quote,$(VERSION_NUMBER))", \
			"revision": "$(call json_quote,$(REVISION))", \
			"target": "$(call json_quote,$(TARGETID))", \
			"board": "$(call json_quote,$(if $(BOARD_NAME),$(BOARD_NAME),$(DEVICE_NAME)))" \
		} \
	}'

define Build/append-gl-metadata
	$(if $(SUPPORTED_DEVICES),-echo $(call metadata_gl_json,$(SUPPORTED_DEVICES)) | fwtool -I - $@)
	sha256sum "$@" | cut -d" " -f1 > "$@.sha256sum"
	[ ! -s "$(BUILD_KEY)" -o ! -s "$(BUILD_KEY).ucert" -o ! -s "$@" ] || { \
		cp "$(BUILD_KEY).ucert" "$@.ucert" ;\
		usign -S -m "$@" -s "$(BUILD_KEY)" -x "$@.sig" ;\
		ucert -A -c "$@.ucert" -x "$@.sig" ;\
		fwtool -S "$@.ucert" "$@" ;\
	}
endef

define Build/zyxel-nwa-fit-filogic
	$(TOPDIR)/scripts/mkits-zyxel-fit-filogic.sh \
		$@.its $@ "80 e1 ff ff ff ff ff ff ff ff"
	PATH=$(LINUX_DIR)/scripts/dtc:$(PATH) mkimage -f $@.its $@.new
	@mv $@.new $@
endef

define Build/cetron-header
	$(eval magic=$(word 1,$(1)))
	$(eval model=$(word 2,$(1)))
	( \
		dd if=/dev/zero bs=856 count=1 2>/dev/null; \
		printf "$(model)," | dd bs=128 count=1 conv=sync 2>/dev/null; \
		md5sum $@ | cut -f1 -d" " | dd bs=32 count=1 2>/dev/null; \
		printf "$(magic)" | dd bs=4 count=1 conv=sync 2>/dev/null; \
		cat $@; \
	) > $@.tmp
	fw_crc=$$(gzip -c $@.tmp | tail -c 8 | od -An -N4 -tx4 --endian little | tr -d ' \n'); \
	printf "$$(echo $$fw_crc | sed 's/../\\x&/g')" | cat - $@.tmp > $@
	rm $@.tmp
endef

define Device/cmcc_rax3000m
  DEVICE_VENDOR := CMCC
  DEVICE_MODEL := RAX3000M
  DEVICE_ALT0_VENDOR := CMCC
  DEVICE_ALT0_MODEL := RAX3000Me
  DEVICE_DTS := mt7981b-cmcc-rax3000m
  DEVICE_DTS_OVERLAY := mt7981b-cmcc-rax3000m-emmc mt7981b-cmcc-rax3000m-nand
  DEVICE_DTS_DIR := ../dts
  DEVICE_DTC_FLAGS := --pad 4096
  DEVICE_DTS_LOADADDR := 0x43f00000
  DEVICE_PACKAGES := kmod-mt7915e kmod-mt7981-firmware mt7981-wo-firmware kmod-usb3 \
	e2fsprogs f2fsck mkf2fs
  KERNEL_LOADADDR := 0x44000000
  KERNEL := kernel-bin | gzip
  KERNEL_INITRAMFS := kernel-bin | lzma | \
	fit lzma $$(KDIR)/image-$$(firstword $$(DEVICE_DTS)).dtb with-initrd | pad-to 64k
  KERNEL_INITRAMFS_SUFFIX := -recovery.itb
  KERNEL_IN_UBI := 1
  UBOOTENV_IN_UBI := 1
  IMAGES := sysupgrade.itb
  IMAGE_SIZE := $$(shell expr 64 + $$(CONFIG_TARGET_ROOTFS_PARTSIZE))m
  IMAGE/sysupgrade.itb := append-kernel | \
	 fit gzip $$(KDIR)/image-$$(firstword $$(DEVICE_DTS)).dtb external-static-with-rootfs | \
	 pad-rootfs | append-metadata
  ARTIFACTS := \
	emmc-gpt.bin emmc-preloader.bin emmc-bl31-uboot.fip \
	nand-preloader.bin nand-bl31-uboot.fip
  ARTIFACT/emmc-gpt.bin := mt798x-gpt emmc
  ARTIFACT/emmc-preloader.bin := mt7981-bl2 emmc-ddr4
  ARTIFACT/emmc-bl31-uboot.fip := mt7981-bl31-uboot cmcc_rax3000m-emmc
  ARTIFACT/nand-preloader.bin := mt7981-bl2 spim-nand-ddr4
  ARTIFACT/nand-bl31-uboot.fip := mt7981-bl31-uboot cmcc_rax3000m-nand
endef
TARGET_DEVICES += cmcc_rax3000m
