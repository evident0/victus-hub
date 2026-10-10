use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn driver_source() -> String {
    fs::read_to_string(repo_root().join("kernel/hp-wmi/hp-wmi.c")).expect("hp-wmi.c")
}

fn cc_ready() -> bool {
    Command::new("cc").arg("--version").output().is_ok_and(|output| output.status.success())
}

fn ident_at(line: &str, name: &str) -> Option<usize> {
    let bytes = line.as_bytes();
    let mut start = 0;
    while let Some(rel) = line[start..].find(name) {
        let at = start + rel;
        let before = at == 0 || !bytes[at - 1].is_ascii_alphanumeric() && bytes[at - 1] != b'_';
        let after = at + name.len();
        let after_ok = after == line.len() || !bytes[after].is_ascii_alphanumeric() && bytes[after] != b'_';
        if before && after_ok {
            return Some(at);
        }
        start = at + 1;
    }
    None
}

fn extract_function(source: &str, name: &str) -> String {
    let mut search_from = 0;
    while search_from < source.len() {
        let line_start = if search_from == 0 && source.starts_with("static ") {
            0
        } else {
            match source[search_from..].find("\nstatic ") {
                Some(rel) => search_from + rel + 1,
                None => break,
            }
        };
        let line_end = source[line_start..].find('\n').map_or(source.len(), |rel| line_start + rel);
        let line = &source[line_start..line_end];
        if let Some(pos) = ident_at(line, name) {
            if line[pos + name.len()..].starts_with('(') {
                let from_paren = line_start + pos + name.len();
                let mut index = from_paren;
                let bytes = source.as_bytes();
                let mut open = None;
                while index < source.len() {
                    if bytes[index] == b';' {
                        break;
                    }
                    if bytes[index] == b'\n' && source[index + 1..].starts_with('{') {
                        open = Some(index + 1);
                        break;
                    }
                    index += 1;
                }
                if let Some(open) = open {
                    let rest = &source[open + 1..];
                    let close = rest.find("\n}").map(|rel| open + 1 + rel + 1).expect(name);
                    return source[line_start..=close].to_owned();
                }
            }
        }
        if line_end >= source.len() {
            break;
        }
        search_from = if line_start == 0 && search_from == 0 && !source.starts_with("static ") {
            line_end + 1
        } else if line_start == search_from {
            line_end + 1
        } else {
            line_end + 1
        };
        if search_from <= line_start {
            search_from = line_end + 1;
        }
    }
    panic!("missing function {name}");
}

fn extract_struct(source: &str, name: &str) -> String {
    let token = format!("struct {name} {{");
    let mut search = 0;
    while let Some(rel) = source[search..].find(&token) {
        let at = search + rel;
        if at == 0 || source.as_bytes()[at - 1] == b'\n' {
            let rest = &source[at + token.len()..];
            let close = rest.find("\n}").map(|rel| at + token.len() + rel + 1).expect(name);
            let mut end = close + 1;
            while end < source.len() && source.as_bytes()[end] != b';' {
                end += 1;
            }
            assert!(end < source.len(), "struct {name} has no semicolon");
            return source[at..=end].to_owned();
        }
        search = at + 1;
    }
    panic!("missing struct {name}");
}

fn extract_define(source: &str, prefix: &str) -> String {
    source.lines().find(|line| line.starts_with(prefix)).unwrap_or_else(|| panic!("missing {prefix}")).to_owned()
}

fn run_c(source: &str) {
    if !cc_ready() {
        eprintln!("cc is missing; skipped C harness");
        return;
    }
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let dir = std::env::temp_dir().join(format!(
        "victus-c-fan-settings-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&dir).unwrap();
    let c_file = dir.join("test.c");
    let executable = dir.join("test");
    fs::write(&c_file, source).unwrap();
    let compiled = cc_output(&dir, |command| {
        command
            .args(["-std=gnu11", "-Wall", "-Wextra", "-Werror"])
            .arg(&c_file)
            .arg("-o")
            .arg(&executable);
    });
    assert!(
        compiled.status.success(),
        "cc failed\n{}{}",
        String::from_utf8_lossy(&compiled.stdout),
        String::from_utf8_lossy(&compiled.stderr)
    );
    let ran = exec_when_ready(&executable);
    let _ = fs::remove_dir_all(&dir);
    assert!(
        ran.status.success(),
        "harness failed\n{}{}",
        String::from_utf8_lossy(&ran.stdout),
        String::from_utf8_lossy(&ran.stderr)
    );
}

fn cc_output(dir: &std::path::Path, configure: impl FnOnce(&mut Command)) -> std::process::Output {
    let mut command = Command::new("cc");
    command.env("CCACHE_DISABLE", "1").env("CCACHE_TEMPDIR", dir);
    configure(&mut command);
    exec_when_ready_command(&mut command)
}

fn exec_when_ready(executable: &std::path::Path) -> std::process::Output {
    let mut command = Command::new(executable);
    exec_when_ready_command(&mut command)
}

fn exec_when_ready_command(command: &mut Command) -> std::process::Output {
    let mut last = String::new();
    for attempt in 0..8 {
        match command.output() {
            Ok(output) => return output,
            Err(err) if err.kind() == std::io::ErrorKind::ExecutableFileBusy && attempt < 7 => {
                last = err.to_string();
                std::thread::sleep(std::time::Duration::from_millis(25 * (attempt as u64 + 1)));
            }
            Err(err) => panic!("{err}"),
        }
    }
    panic!("executable stayed busy: {last}");
}

fn joined(source: &str, names: &[&str], kind: &str) -> String {
    names
        .iter()
        .map(|name| if kind == "fn" { extract_function(source, name) } else { extract_struct(source, name) })
        .collect::<Vec<_>>()
        .join("\n")
}


const PIECE_0: &str = r#"
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned short umode_t;
enum hwmon_sensor_types { hwmon_pwm, hwmon_fan };
enum { hwmon_pwm_input, hwmon_pwm_enable };
struct mutex { int unused; };
struct delayed_work { int unused; };
#define __packed __attribute__((packed))
#define U8_MAX UINT8_MAX
#define CPU_FAN 0
#define GPU_FAN 1
#define PWM_MODE_AUTO 2
#define HPWMI_VICTUS_S_GET_FAN_TABLE_QUERY 0
#define HPWMI_GM 0
#define pr_warn(...) ((void)0)
#define pr_info(...) ((void)0)
"#;

const PIECE_1: &str = r#"
"#;

const PIECE_2: &str = r#"
    static int modern_rpm[2], legacy_rpm[2], table_result;
    static int tachometer_reads;
    static u8 table_data[128];
    static bool force_fan_control_support;
    static bool unsafe_board;
    static int modern_reader(int fan) { tachometer_reads++; return modern_rpm[fan]; }
    static int legacy_reader(int fan) { tachometer_reads++; return legacy_rpm[fan]; }
static const struct hp_wmi_fan_profile_params victus_s_fan_profile_params = {
    .get_fan_speed = modern_reader, .fan_table = true,
};
static const struct hp_wmi_fan_profile_params legacy_fan_profile_params = {
    .get_fan_speed = legacy_reader,
};
static const struct hp_wmi_fan_profile_params *board_profile;
static const struct hp_wmi_fan_profile_params *hp_wmi_fan_profile(void)
{ return board_profile; }
    static bool hp_wmi_unsafe_fan_board(void) { return unsafe_board; }
static bool hp_wmi_fan_table_supported(void)
{ return board_profile && board_profile->fan_table; }
static int hp_wmi_perform_query(int query, int command, void *data, int in, int out)
{
    (void)query; (void)command; (void)in;
    assert(out == sizeof(table_data));
    memcpy(data, table_data, sizeof(table_data));
    return table_result;
}
"#;

const PIECE_3: &str = r#"
int main(void)
{
    struct hp_wmi_hwmon_priv priv = {0};
    struct victus_s_fan_table *table = (void *)table_data;

    /* Unlisted boards get real manual availability and the legacy reader. */
    force_fan_control_support = true;
    legacy_rpm[0] = 2400; legacy_rpm[1] = 2600;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv));
    assert(priv.min_rpm == 0 && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, GPU_FAN) == 2600);

    /* Without force, an unlisted board retains monitoring only. */
    force_fan_control_support = false;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(!hp_wmi_fan_control_supported(&priv));

    /* A valid table keeps its measured limits without requiring force. */
    board_profile = &victus_s_fan_profile_params;
    table->header.num_fans = 2;
    table->entries[0] = (struct victus_s_fan_table_entry){20, 22, 30};
    table->entries[1] = (struct victus_s_fan_table_entry){55, 58, 40};
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv));
    assert(priv.min_rpm == 20 && priv.max_rpm == 55);

    /* A valid table must not prevent switching away from a broken reader. */
    modern_rpm[0] = modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &legacy_fan_profile_params);
    legacy_rpm[0] = legacy_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(!hp_wmi_fan_control_supported(&priv));
    force_fan_control_support = true;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.max_rpm == 55 && hp_wmi_fan_control_supported(&priv));
    legacy_rpm[0] = 2400; legacy_rpm[1] = 2600;

    /* Nonzero but bogus tables (8BBE-style 1800 RPM limit) use fallback. */
    table->entries[0] = (struct victus_s_fan_table_entry){10, 12, 30};
    table->entries[1] = (struct victus_s_fan_table_entry){18, 20, 40};
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.max_rpm == 60);
    force_fan_control_support = false;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(!hp_wmi_fan_control_supported(&priv));

    /* Probe errors leave EC/Max operational with nonzero fallback limits. */
    table_result = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.max_rpm == 60);
    assert(!hp_wmi_fan_control_supported(&priv));
    table_result = 3; /* Positive firmware status must become a Linux errno. */
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    table_result = 0;
    memset(table_data, 0, sizeof(table_data));
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    table->header.num_fans = 2; /* Header present, but no usable entries. */
    assert(hp_wmi_setup_fan_settings(&priv) == 0);

    /* One failed modern channel selects the working legacy reader for both. */
    force_fan_control_support = true;
    modern_rpm[0] = 2000; modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &legacy_fan_profile_params);
    assert(hp_wmi_fan_control_supported(&priv) && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, GPU_FAN) == 2600);

    /* Failed table query still uses working modern tachometers. */
    table_result = -EIO;
    modern_rpm[1] = 2800;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &victus_s_fan_profile_params);

    /* Unlisted cross-over BIOS: legacy fails, modern succeeds. */
    board_profile = NULL;
    legacy_rpm[0] = legacy_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &victus_s_fan_profile_params);

    /* Total probe failure may be forced, but read failures remain visible. */
    modern_rpm[0] = modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv) && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, CPU_FAN) == -EIO);

    /* Even an explicit force option cannot probe a known abort-prone EC. */
    unsafe_board = true;
    board_profile = NULL;
    legacy_rpm[0] = legacy_rpm[1] = 2400;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(!hp_wmi_fan_control_supported(&priv));
    assert(priv.fan_profile == NULL);
    int reads_before = tachometer_reads;
    assert(hp_wmi_hwmon_is_visible(&priv, hwmon_fan, 0, CPU_FAN) == 0);
    assert(hp_wmi_hwmon_is_visible(&priv, hwmon_fan, 0, GPU_FAN) == 0);
    assert(hp_wmi_hwmon_is_visible(&priv, hwmon_pwm, hwmon_pwm_input, CPU_FAN) == 0);
    assert(hp_wmi_hwmon_is_visible(&priv, hwmon_pwm, hwmon_pwm_enable, CPU_FAN) == 0644);
    assert(tachometer_reads == reads_before);
    return 0;
}
"#;

#[test]
fn test_firmware_probe_and_fallback_paths() {
    let driver = driver_source();
    let declarations = joined(&driver, &["hp_wmi_fan_profile_params", "hp_wmi_hwmon_priv", "victus_s_fan_table_header", "victus_s_fan_table_entry", "victus_s_fan_table"], "st");
    let fallback = extract_define(&driver, "#define VICTUS_S_FALLBACK_MAX_RPM_FW");
    let functions = joined(&driver, &["hp_wmi_fan_control_supported", "hp_wmi_get_active_fan_speed", "hp_wmi_set_fallback_fan_limits", "hp_wmi_fan_speed_probe", "hp_wmi_select_fan_reader", "hp_wmi_setup_fallback_fan_settings", "hp_wmi_setup_fan_settings", "hp_wmi_hwmon_is_visible"], "fn");
    let mut source = String::new();
    source.push_str(PIECE_0);
    source.push_str(&declarations);
    source.push_str(PIECE_1);
    source.push_str(&fallback);
    source.push_str(PIECE_2);
    source.push_str(&functions);
    source.push_str(PIECE_3);
    run_c(&source);
}
