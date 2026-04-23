REQUIRE_IMAGE_METADATA=1

platform_do_upgrade() {
	local board=$(board_name)

	case "$board" in
	cmcc,rax3000m)
		case "$(cmdline_get_var root)" in
		/dev/mmc*)
			CI_KERNPART="production"
			emmc_do_upgrade "$1"
			;;
		*)
			CI_KERNPART="fit"
			nand_do_upgrade "$1"
			;;
		esac
		;;
	*)
		nand_do_upgrade "$1"
		;;
	esac
}

PART_NAME=firmware

platform_check_image() {
	local board=$(board_name)
	local magic="$(get_magic_long "$1")"

	[ "$#" -gt 1 ] && return 1

	case "$board" in
	cmcc,rax3000m)
		[ "$magic" != "d00dfeed" ] && {
			echo "Invalid image type."
			return 1
		}
		return 0
		;;
	*)
		nand_do_platform_check "$board" "$1"
		return $?
		;;
	esac

	return 0
}

platform_copy_config() {
	case "$(board_name)" in
	cmcc,rax3000m)
		case "$(cmdline_get_var root)" in
		/dev/mmc*)
			emmc_copy_config
			;;
		esac
		;;
	esac
}

platform_pre_upgrade() {
	local board=$(board_name)
}
