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
