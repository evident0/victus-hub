use super::*;
use victus_core::offline_scratch;

#[test]
fn discovery_reads_only_the_scratch_tree() {
    let root = offline_scratch("keys");
    assert!(root.starts_with(std::env::temp_dir()));
    let by_path = root.join("by-path");
    fs::create_dir_all(&by_path).unwrap();
    fs::write(by_path.join("platform-i8042-serio-0-event-kbd"), b"").unwrap();
    let sys = root.join("class/input/input0");
    fs::create_dir_all(&sys).unwrap();
    fs::write(sys.join("name"), "HP WMI hotkeys\n").unwrap();
    fs::create_dir_all(sys.join("event5")).unwrap();
    let dev = root.join("dev");
    fs::create_dir_all(&dev).unwrap();
    fs::write(dev.join("event5"), b"").unwrap();
    let found = discover_keyboards(&root.join("class/input"), &by_path, &dev);
    assert!(found.iter().all(|path| path.starts_with(&root)));
    assert!(found.iter().any(|path| path.ends_with("platform-i8042-serio-0-event-kbd")));
    assert!(found.iter().any(|path| path.ends_with("event5")));
    let _ = fs::remove_dir_all(root);
}
