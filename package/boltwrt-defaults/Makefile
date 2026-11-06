include $(TOPDIR)/rules.mk

PKG_NAME:=boltwrt-defaults
PKG_RELEASE:=3
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/boltwrt-defaults
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=BoltWrt initial configuration and upgrade migrations
  DEPENDS:=+base-files +uci +jshn
  PKGARCH:=all
endef

define Build/Compile
endef

define Package/boltwrt-defaults/install
	$(INSTALL_DIR) $(1)

	$(CP) ./files/* $(1)/
endef

$(eval $(call BuildPackage,boltwrt-defaults))
