define KernelPackage/iio-mt6577-auxadc
  TITLE:=Mediatek AUXADC driver
  DEPENDS:=@TARGET_mediatek_filogic
  KCONFIG:=CONFIG_MEDIATEK_MT6577_AUXADC
  FILES:= \
	$(LINUX_DIR)/drivers/iio/adc/mt6577_auxadc.ko
  AUTOLOAD:=$(call AutoProbe,mt6577_auxadc)
  $(call AddDepends/iio)
endef
$(eval $(call KernelPackage,iio-mt6577-auxadc))
