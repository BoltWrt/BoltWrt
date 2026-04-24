REQUIRE_IMAGE_METADATA=1
RAMFS_COPY_BIN='fitblk'

platform_do_upgrade() {
	fit_do_upgrade "$1"
}

PART_NAME=firmware

platform_check_image() {
	local magic="$(get_magic_long "$1")"

	[ "$#" -gt 1 ] && return 1

	[ "$magic" != "d00dfeed" ] && {
		echo "Invalid image type."
		return 1
	}
	return 0
}

platform_copy_config() {
	if [ "$CI_METHOD" = "emmc" ]; then
		emmc_copy_config
	fi
}

platform_pre_upgrade() {}
