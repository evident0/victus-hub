use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use victus_core::{HubError, HubResult};

use crate::auth::Peer;
use crate::dispatch::dispatch;
use crate::platform::Platform;
use crate::runtime::Runtime;

const MAX_CLIENTS: usize = 32;
const MAX_REQUEST: usize = 64 * 1024;

pub fn serve<P, F>(path: &Path, runtime: Arc<Mutex<Runtime<P>>>, auth: F, stop: Arc<AtomicBool>) -> HubResult<()>
where
    P: Platform + 'static,
    F: Fn(u32, i32) -> Peer + Send + Sync + 'static,
{
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| HubError::new(format!("socket directory: {error}")))?;
    }
    if path.exists() {
        fs::remove_file(path).map_err(|error| HubError::new(format!("stale socket: {error}")))?;
    }
    let listener = UnixListener::bind(path).map_err(|error| HubError::new(format!("bind {}: {error}", path.display())))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o666)).map_err(|error| HubError::new(format!("socket mode: {error}")))?;
    listener.set_nonblocking(true).map_err(|error| HubError::new(format!("socket listen: {error}")))?;

    let (light_tx, light_rx) = mpsc::channel::<String>();
    runtime.lock().expect("runtime lock").set_lighting_sender(light_tx);
    let subscribers = Arc::new(Mutex::new(Vec::<Sender<String>>::new()));
    let fanout = Arc::clone(&subscribers);
    thread::spawn(move || {
        while let Ok(line) = light_rx.recv() {
            if let Ok(mut guard) = fanout.lock() {
                guard.retain(|sender| sender.send(line.clone()).is_ok());
            }
        }
    });

    let auth = Arc::new(auth);
    let clients = Arc::new(AtomicUsize::new(0));
    while !stop.load(Ordering::Relaxed) {
        match listener.accept() {
            Ok((stream, _)) => {
                if clients.fetch_add(1, Ordering::Relaxed) >= MAX_CLIENTS {
                    clients.fetch_sub(1, Ordering::Relaxed);
                    let _ = write_line(&stream, "ERR\ttoo many clients\n");
                    continue;
                }
                let runtime = Arc::clone(&runtime);
                let auth = Arc::clone(&auth);
                let stop = Arc::clone(&stop);
                let subscribers = Arc::clone(&subscribers);
                let clients = Arc::clone(&clients);
                thread::spawn(move || {
                    let _ = handle_client(stream, &runtime, auth.as_ref(), &subscribers, &stop);
                    clients.fetch_sub(1, Ordering::Relaxed);
                });
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(HubError::new(format!("accept: {error}"))),
        }
    }
    let _ = fs::remove_file(path);
    Ok(())
}

fn handle_client<P: Platform>(
    mut stream: UnixStream,
    runtime: &Arc<Mutex<Runtime<P>>>,
    auth: &dyn Fn(u32, i32) -> Peer,
    subscribers: &Arc<Mutex<Vec<Sender<String>>>>,
    stop: &AtomicBool,
) -> HubResult<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();
    let (uid, pid) = peer_ids(&stream)?;
    let peer = auth(uid, pid);
    if !peer.authorized {
        write_line(&stream, "ERR\taccess denied: an active, unlocked local desktop session is required\n")?;
        return Ok(());
    }
    let Some(line) = read_line(&mut stream)? else {
        write_line(&stream, "ERR\tempty request\n")?;
        return Ok(());
    };
    if line == "shortcut-events" {
        write_line(&stream, "OK\tshortcut-events\n")?;
        stream_events(stream, subscribers, stop)?;
        return Ok(());
    }
    let response = {
        let mut guard = runtime.lock().expect("runtime lock");
        dispatch(&mut guard, &peer, &line)
    };
    write_line(&stream, &response)?;
    Ok(())
}

fn stream_events(mut stream: UnixStream, subscribers: &Arc<Mutex<Vec<Sender<String>>>>, stop: &AtomicBool) -> HubResult<()> {
    let (sender, receiver) = mpsc::channel();
    subscribers.lock().expect("subscribers").push(sender);
    stream.set_nonblocking(true).ok();
    while !stop.load(Ordering::Relaxed) {
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(line) => {
                if stream.write_all(line.as_bytes()).is_err() {
                    break;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let mut byte = [0_u8; 1];
                match stream.read(&mut byte) {
                    Ok(0) | Ok(_) => break,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(_) => break,
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    Ok(())
}

fn read_line(stream: &mut UnixStream) -> HubResult<Option<String>> {
    let mut buffer = Vec::new();
    let mut byte = [0_u8; 1];
    loop {
        match stream.read(&mut byte) {
            Ok(0) => break,
            Ok(_) => {
                if byte[0] == b'\n' {
                    break;
                }
                buffer.push(byte[0]);
                if buffer.len() > MAX_REQUEST {
                    return Err(HubError::new("request is too large"));
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock || error.kind() == std::io::ErrorKind::TimedOut => {
                return Err(HubError::new("request timed out"));
            }
            Err(error) => return Err(HubError::new(format!("request read: {error}"))),
        }
    }
    if buffer.is_empty() {
        return Ok(None);
    }
    String::from_utf8(buffer).map(Some).map_err(|_| HubError::new("request is not utf-8"))
}

fn write_line(mut stream: &UnixStream, line: &str) -> HubResult<()> {
    stream.write_all(line.as_bytes()).map_err(|error| HubError::new(format!("response write: {error}")))
}

fn peer_ids(stream: &UnixStream) -> HubResult<(u32, i32)> {
    let creds = nix::sys::socket::getsockopt(stream, nix::sys::socket::sockopt::PeerCredentials)
        .map_err(|error| HubError::new(format!("peer credentials: {error}")))?;
    Ok((creds.uid(), creds.pid()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::FakePlatform;
    use victus_core::{offline_scratch, transact, DEFAULT_SOCKET};

    #[test]
    fn server_answers_on_a_temporary_socket() {
        let dir = offline_scratch("serve");
        let path = dir.join("hub.sock");
        assert!(path.starts_with(std::env::temp_dir()));
        assert_ne!(path, Path::new(DEFAULT_SOCKET));
        let runtime = Arc::new(Mutex::new(Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), FakePlatform::default())));
        let stop = Arc::new(AtomicBool::new(false));
        let server_stop = Arc::clone(&stop);
        let server_path = path.clone();
        let server = thread::spawn(move || {
            serve(&server_path, runtime, |_uid, pid| Peer { uid: 0, pid, authorized: true }, server_stop)
        });
        let mut reply = None;
        for _ in 0..50 {
            if let Ok(line) = transact(&path, "get-state", Duration::from_millis(200)) {
                reply = Some(line);
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        stop.store(true, Ordering::Relaxed);
        server.join().expect("server thread").expect("server");
        let reply = reply.expect("temporary socket reply");
        assert!(reply.starts_with("OK\t"), "{reply}");
        let _ = fs::remove_dir_all(dir);
    }
}
