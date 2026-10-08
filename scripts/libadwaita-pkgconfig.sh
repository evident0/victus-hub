# Keep this compatibility entry point read-only. Cargo's GTK bindings require
# real development metadata; fabricating a version can link unavailable APIs.
ensure_libadwaita_pkgconfig() {
	return 0
}
