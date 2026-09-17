# Shared terminal verbosity argument parsing. Source before any setup actions.
if [ "$#" -gt 1 ]; then
	printf 'Usage: %s [0|1|2|3]\n' "$0" >&2
	exit 2
fi
case "${1:-0}" in
	0|1|2|3) export VICTUS_HUB_DEBUG_LEVEL="${1:-0}" ;;
	*)
		printf 'Invalid debug level: %s. Expected 0, 1, 2 or 3.\n' "$1" >&2
		exit 2
		;;
esac
