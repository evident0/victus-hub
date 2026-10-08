use super::*;
use crate::platform::FakePlatform;
use victus_core::{offline_scratch, transact, DEFAULT_SOCKET};

#[test]
fn locked_subscriber_disconnects_and_is_removed() {
    let (server, mut client) = UnixStream::pair().unwrap();
    client.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
    let subscribers = Arc::new(Mutex::new(Vec::new()));
    let server_subscribers = Arc::clone(&subscribers);
    let worker = thread::spawn(move || {
        let stop = AtomicBool::new(false);
        let peer = Peer { uid: 1000, pid: 1, authorized: true };
        stream_events(server, &server_subscribers, &stop, &peer,
            &|uid, pid| Peer { uid, pid, authorized: false }, || None).unwrap();
    });
    let mut sent = false;
    for _ in 0..100 {
        if let Some(sender) = subscribers.lock().unwrap().first() {
            sender.send("STATE\t{}\n".into()).unwrap();
            sent = true;
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    assert!(sent);
    let mut bytes = [0_u8; 1];
    assert_eq!(client.read(&mut bytes).unwrap(), 0);
    worker.join().unwrap();
    assert!(subscribers.lock().unwrap().is_empty());
}

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

#[test]
fn multiline_and_oversized_requests_are_rejected() {
    let (mut reader, mut writer) = UnixStream::pair().unwrap();
    writer.write_all(b"fan-max\nfan-auto\n").unwrap();
    drop(writer);
    let error = super::read_line(&mut reader).unwrap_err();
    assert!(error.to_string().contains("one request"), "{error}");

    let (mut reader, mut writer) = UnixStream::pair().unwrap();
    let payload = vec![b'a'; super::MAX_REQUEST + 1];
    let worker = thread::spawn(move || {
        let _ = writer.write_all(&payload);
    });
    let error = super::read_line(&mut reader).unwrap_err();
    let _ = worker.join();
    assert!(error.to_string().contains("too large"), "{error}");
}

#[test]
fn authorization_is_rechecked_after_the_request_arrives() {
    let dir = offline_scratch("reauthorize");
    let path = dir.join("hub.sock");
    let runtime = Arc::new(Mutex::new(Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), FakePlatform::default())));
    let allowed = Arc::new(AtomicBool::new(true));
    let stop = Arc::new(AtomicBool::new(false));
    let (checked_tx, checked_rx) = mpsc::sync_channel(2);
    let server_path = path.clone();
    let server_stop = Arc::clone(&stop);
    let server_allowed = Arc::clone(&allowed);
    let server = thread::spawn(move || serve(&server_path, runtime, move |_, pid| {
        let authorized = server_allowed.load(Ordering::Relaxed);
        let _ = checked_tx.try_send(());
        Peer { uid: 1000, pid, authorized }
    }, server_stop));
    let mut stream = None;
    for _ in 0..100 {
        if let Ok(connection) = UnixStream::connect(&path) { stream = Some(connection); break; }
        thread::sleep(Duration::from_millis(10));
    }
    let mut stream = stream.expect("temporary server");
    stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
    stream.write_all(b"get-state").unwrap();
    checked_rx.recv_timeout(Duration::from_secs(3)).unwrap();
    allowed.store(false, Ordering::Relaxed);
    stream.write_all(b"\n").unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    stop.store(true, Ordering::Relaxed);
    server.join().unwrap().unwrap();
    assert!(response.starts_with("ERR\taccess denied"), "{response}");
    let _ = fs::remove_dir_all(dir);
}
