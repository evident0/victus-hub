use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

use crate::{HubError, HubResult};

pub const DEFAULT_SOCKET: &str = "/run/victus-hubd/victus-hub.sock";

/// Send one request line and read one response line.
///
/// Callers pass the socket path. Library tests use a temporary socket and
/// never the installed daemon socket.
pub fn transact(socket: &Path, request: &str, timeout: Duration) -> HubResult<String> {
    let mut stream = UnixStream::connect(socket).map_err(|error| HubError::new(format!("daemon socket: {error}")))?;
    stream.set_read_timeout(Some(timeout)).ok();
    stream.set_write_timeout(Some(timeout)).ok();
    let mut line = request.to_owned();
    if !line.ends_with('\n') {
        line.push('\n');
    }
    stream.write_all(line.as_bytes()).map_err(|error| HubError::new(format!("daemon write: {error}")))?;
    let mut buffer = Vec::new();
    BufReader::new(stream).take(1_048_577).read_until(b'\n', &mut buffer)
        .map_err(|error| HubError::new(format!("daemon read: {error}")))?;
    if buffer.len() > 1_048_576 { return Err(HubError::new("daemon response is too large")); }
    if buffer.is_empty() {
        return Err(HubError::new("empty response"));
    }
    String::from_utf8(buffer).map_err(|_| HubError::new("daemon response is not utf-8"))
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/client.rs"]
mod tests;
