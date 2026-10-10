/// Session authorization. Lookup is injected so tests never call loginctl.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionInfo {
    pub user: u32,
    pub active: bool,
    pub remote: bool,
    pub locked: bool,
    pub kind: String,
    pub seat: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Peer {
    pub uid: u32,
    pub pid: i32,
    pub authorized: bool,
}

pub fn session_ok(uid: u32, info: &SessionInfo) -> bool {
    info.user == uid
        && info.active
        && !info.remote
        && !info.locked
        && matches!(info.kind.as_str(), "x11" | "wayland")
        && info.seat == "seat0"
}

pub fn authorize(uid: u32, pid: i32, session: &str, lookup: impl FnOnce(&str) -> Option<SessionInfo>) -> Peer {
    let authorized = if uid == 0 {
        true
    } else if session.is_empty() {
        false
    } else {
        lookup(session).is_some_and(|info| session_ok(uid, &info))
    };
    Peer { uid, pid, authorized }
}

pub fn parse_loginctl(stdout: &str) -> Option<SessionInfo> {
    let mut user = None;
    let mut active = None;
    let mut remote = None;
    let mut locked = None;
    let mut kind = None;
    let mut seat = None;
    for line in stdout.lines() {
        let Some((key, value)) = line.split_once('=') else { continue };
        match key {
            "User" => user = value.parse().ok(),
            "Active" => active = Some(value == "yes"),
            "Remote" => remote = Some(value != "no"),
            "LockedHint" => locked = Some(value != "no"),
            "Type" => kind = Some(value.to_owned()),
            "Seat" => seat = Some(value.to_owned()),
            _ => {}
        }
    }
    Some(SessionInfo {
        user: user?,
        active: active?,
        remote: remote?,
        locked: locked?,
        kind: kind?,
        seat: seat?,
    })
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hubd/auth.rs"]
mod tests;
