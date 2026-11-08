include $(TOPDIR)/rules.mk

PKG_NAME:=luci-boltwrt-upload
PKG_VERSION:=1.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/luci-boltwrt-upload
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=Web Servers/Proxies
  TITLE:=BoltWrt LuCI upload streaming
  DEPENDS:=+nginx-mod-luci
  PKGARCH:=all
endef

define Build/Compile
endef

define Package/luci-boltwrt-upload/install
	$(INSTALL_DIR) $(1)/etc/nginx/conf.d
	$(INSTALL_CONF) ./files/00-boltwrt-luci-upload.locations $(1)/etc/nginx/conf.d/
endef

$(eval $(call BuildPackage,luci-boltwrt-upload))
