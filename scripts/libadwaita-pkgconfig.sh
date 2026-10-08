# Point pkg-config at the installed libadwaita runtime when the devel
# package is absent. Always returns 0. Bindings that need headers still
# require libadwaita-devel.
ensure_libadwaita_pkgconfig() {
	if pkg-config --exists libadwaita-1 2>/dev/null; then
		return 0
	fi
	local dir soname stub
	soname=
	for dir in /usr/lib64 /lib64 /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu; do
		if [ -e "$dir/libadwaita-1.so.0" ]; then
			soname=$dir/libadwaita-1.so.0
			break
		fi
	done
	if [ -z "$soname" ]; then
		return 0
	fi
	stub=$(mktemp -d)
	ln -s "$soname" "$stub/libadwaita-1.so"
	cat >"$stub/libadwaita-1.pc" <<EOF
prefix=/usr
libdir=$stub
includedir=/usr/include

Name: libadwaita
Description: libadwaita runtime shim
Version: 1.5.0
Libs: -L$stub -ladwaita-1
Cflags: -I/usr/include
EOF
	if [ -n "${PKG_CONFIG_PATH:-}" ]; then
		PKG_CONFIG_PATH="$stub:${PKG_CONFIG_PATH}"
	else
		PKG_CONFIG_PATH=$stub
	fi
	export PKG_CONFIG_PATH
	return 0
}
