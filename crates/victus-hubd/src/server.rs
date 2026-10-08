use std::fs;
use std::io::{Read, Write};
use std::os::fd::AsFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use nix::poll::{poll, PollFd, PollFlags};

use victus_core::{HubError, HubResult};

use crate::auth::Peer;
use crate::dispatch::{dispatch, dispatch_with_workers};
use crate::platform::Platform;
use crate::runtime::Runtime;

const MAX_CLIENTS: usize = 32;
const MAX_REQUEST: usize = 64 * 1024;

pub fn serve<P, F>(path: &Path, runtime: Arc<Mutex<Runtime<P>>>, auth: F, stop: Arc<AtomicBool>) -> HubResult<()>
where
    P: Platform + 'static,
    F: Fn(u32, i32) -> Peer + Send + Sync + 'static,
{
    serve_inner(path, runtime, None, auth, stop)
}

pub fn serve_with_workers<P, F>(path: &Path, runtime: Arc<Mutex<Runtime<P>>>, sampler: Arc<Mutex<P>>, commands: Arc<Mutex<P>>, auth: F, stop: Arc<AtomicBool>) -> HubResult<()>
where P: Platform + 'static, F: Fn(u32, i32) -> Peer + Send + Sync + 'static {
    serve_inner(path, runtime, Some((sampler, commands)), auth, stop)
}

fn serve_inner<P, F>(path: &Path, runtime: Arc<Mutex<Runtime<P>>>, workers: Option<(Arc<Mutex<P>>, Arc<Mutex<P>>)>, auth: F, stop: Arc<AtomicBool>) -> HubResult<()>
where P: Platform + 'static, F: Fn(u32, i32) -> Peer + Send + Sync + 'static {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| HubError::new(format!("socket directory: {error}")))?;
    }
    if path.exists() {
        fs::remove_file(path).map_err(|error| HubError::new(format!("stale socket: {error}")))?;
    }
    let listener = UnixListener::bind(path).map_err(|error| HubError::new(format!("bind {}: {error}", path.display())))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o666)).map_err(|error| HubError::new(format!("socket mode: {error}")))?;
    listener.set_nonblocking(true).map_err(|error| HubError::new(format!("socket listen: {error}")))?;

    let (light_tx, light_rx) = mpsc::sync_channel::<String>(32);
    runtime.lock().expect("runtime lock").set_lighting_sender(light_tx);
    let subscribers = Arc::new(Mutex::new(Vec::<Arc<SyncSender<String>>>::new()));
    let fanout = Arc::clone(&subscribers);
    thread::spawn(move || {
        while let Ok(line) = light_rx.recv() {
            if let Ok(mut guard) = fanout.lock() {
                guard.retain(|sender| sender.try_send(line.clone()).is_ok());
            }
        }
    });

    let auth = Arc::new(auth);
    let clients = Arc::new(AtomicUsize::new(0));
    while !stop.load(Ordering::Relaxed) {
        let mut fds = [PollFd::new(listener.as_fd(), PollFlags::POLLIN)];
        if poll(&mut fds, 1000_u16).unwrap_or(0) == 0 { continue; }
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
                let workers = workers.clone();
                thread::spawn(move || {
                    let _ = handle_client(stream, &runtime, workers.as_ref(), auth.as_ref(), &subscribers, &stop);
                    clients.fetch_sub(1, Ordering::Relaxed);
                });
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                continue;
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
    workers: Option<&(Arc<Mutex<P>>, Arc<Mutex<P>>)>,
    auth: &dyn Fn(u32, i32) -> Peer,
    subscribers: &Arc<Mutex<Vec<Arc<SyncSender<String>>>>>,
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
    let peer = auth(uid, pid);
    if !peer.authorized { write_line(&stream, "ERR\taccess denied: session is no longer active and unlocked\n")?; return Ok(()); }
    if line == "shortcut-events" {
        write_line(&stream, "OK\tshortcut-events\n")?;
        stream_events(stream, subscribers, stop, &peer, auth, || {
            let initial = runtime.lock().expect("runtime lock").state_value();
            Some(format!("STATE\t{initial}\n"))
        })?;
        return Ok(());
    }
    let response = if let Some((sampler, commands)) = workers {
        dispatch_with_workers(runtime, sampler, commands, &peer, &line)
    } else {
        let mut guard = runtime.lock().expect("runtime lock");
        dispatch(&mut guard, &peer, &line)
    };
    write_line(&stream, &response)?;
    Ok(())
}

fn stream_events(mut stream: UnixStream, subscribers: &Arc<Mutex<Vec<Arc<SyncSender<String>>>>>, stop: &AtomicBool, peer: &Peer, auth: &dyn Fn(u32, i32) -> Peer, snapshot: impl FnOnce() -> Option<String>) -> HubResult<()> {
    let (sender, receiver) = mpsc::sync_channel(16);
    let sender = Arc::new(sender);
    subscribers.lock().expect("subscribers").push(Arc::clone(&sender));
    struct Subscription<'a> {
        subscribers: &'a Mutex<Vec<Arc<SyncSender<String>>>>,
        sender: std::sync::Weak<SyncSender<String>>,
    }
    impl Drop for Subscription<'_> {
        fn drop(&mut self) {
            if let Ok(mut subscribers) = self.subscribers.lock() {
                subscribers.retain(|sender| Arc::as_ptr(sender) != self.sender.as_ptr());
            }
        }
    }
    let _subscription = Subscription { subscribers, sender: Arc::downgrade(&sender) };
    drop(sender);
    // Register before taking the snapshot so no state change falls in a gap.
    if let Some(initial) = snapshot() { write_line(&stream, &initial)?; }
    stream.set_nonblocking(true).ok();
    while !stop.load(Ordering::Relaxed) {
        match receiver.recv_timeout(Duration::from_secs(1)) {
            Ok(line) => {
                // Reconnecting supplies an authoritative snapshot after unlock.
                // Keeping this stream alive would silently lose the update.
                if !auth(peer.uid, peer.pid).authorized { break; }
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
    let mut chunk = [0_u8; 4096];
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() { return Err(HubError::new("request timed out")); }
        stream.set_read_timeout(Some(remaining)).ok();
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => {
                let end = chunk[..count].iter().position(|byte| *byte == b'\n');
                buffer.extend_from_slice(&chunk[..end.unwrap_or(count)]);
                if buffer.len() > MAX_REQUEST {
                    return Err(HubError::new("request is too large"));
                }
                if let Some(end) = end {
                    if end + 1 != count { return Err(HubError::new("one request per connection is required")); }
                    break;
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
#[path = "../../../tests/rust/victus-hubd/server.rs"]
mod tests;
