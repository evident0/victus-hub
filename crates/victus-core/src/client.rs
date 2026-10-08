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
mod tests {
    use super::*;
    use crate::offline_scratch;
    use std::os::unix::net::UnixListener;
    use std::thread;

    #[test]
    fn client_talks_only_to_the_temporary_socket() {
        let dir = offline_scratch("client");
        let path = dir.join("hub.sock");
        assert!(path.starts_with(std::env::temp_dir()));
        assert_ne!(path, std::path::Path::new(DEFAULT_SOCKET));
        let listener = UnixListener::bind(&path).unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = Vec::new();
            let mut byte = [0_u8; 1];
            loop {
                if stream.read(&mut byte).unwrap() == 0 || byte[0] == b'\n' {
                    break;
                }
                buffer.push(byte[0]);
            }
            assert_eq!(String::from_utf8(buffer).unwrap(), "get-state");
            stream.write_all(b"OK\t{}\n").unwrap();
        });
        let reply = transact(&path, "get-state", Duration::from_secs(2)).unwrap();
        assert_eq!(reply, "OK\t{}\n");
        server.join().unwrap();
        let _ = std::fs::remove_dir_all(dir);
    }
}
