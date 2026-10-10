use super::*;

fn desktop(locked: bool) -> SessionInfo {
    SessionInfo {
        user: 1000,
        active: true,
        remote: false,
        locked,
        kind: "wayland".into(),
        seat: "seat0".into(),
    }
}

#[test]
fn root_is_authorized_without_a_session_lookup() {
    let mut looked = false;
    let peer = authorize(0, 1, "", |_| {
        looked = true;
        None
    });
    assert!(peer.authorized);
    assert!(!looked);
}

#[test]
fn locked_or_missing_sessions_fail_closed() {
    let peer = authorize(1000, 2, "", |_| Some(desktop(false)));
    assert!(!peer.authorized);
    let peer = authorize(1000, 2, "1", |_| Some(desktop(true)));
    assert!(!peer.authorized);
    let peer = authorize(1000, 2, "1", |_| Some(desktop(false)));
    assert!(peer.authorized);
    let text = "User=1000\nActive=yes\nRemote=no\nLockedHint=no\nType=wayland\nSeat=seat0\n";
    assert!(session_ok(1000, &parse_loginctl(text).unwrap()));
}

#[test]
fn session_matrix_matches_python() {
    let text = |changes: &[(&str, &str)]| {
        let mut values = [
            ("User", "1000"),
            ("Active", "yes"),
            ("Remote", "no"),
            ("LockedHint", "no"),
            ("Type", "wayland"),
            ("Seat", "seat0"),
        ];
        for (key, value) in changes {
            if let Some(slot) = values.iter_mut().find(|(name, _)| name == key) {
                slot.1 = value;
            }
        }
        values.iter().map(|(key, value)| format!("{key}={value}")).collect::<Vec<_>>().join("\n")
    };
    for (changes, expected) in [
        (&[][..], true),
        (&[("Type", "x11")][..], true),
        (&[("Remote", "yes")], false),
        (&[("Active", "no")], false),
        (&[("LockedHint", "yes")], false),
        (&[("LockedHint", "")], false),
        (&[("User", "1001")], false),
        (&[("Type", "tty")], false),
        (&[("Seat", "seat1")], false),
    ] {
        let info = parse_loginctl(&text(changes)).unwrap();
        assert_eq!(session_ok(1000, &info), expected, "{changes:?} {info:?}");
    }
    let peer = authorize(1000, 2, "1", |_| None);
    assert!(!peer.authorized);
    assert!(parse_loginctl("User=1000\nActive=yes\n").is_none());
}
