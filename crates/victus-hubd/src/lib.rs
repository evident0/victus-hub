//! Victus Hub root daemon library.
//!
//! Socket serving takes an explicit path. The installed socket
//! `/run/victus-hubd/victus-hub.sock` is used only by the `victus-hubd` binary.

#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc, clippy::must_use_candidate)]
#![allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
#![allow(clippy::module_name_repetitions, clippy::doc_markdown, clippy::too_many_arguments, clippy::too_many_lines)]

mod auth;
mod binds;
mod dispatch;
mod keys;
mod platform;
mod runtime;
mod server;
mod system;

pub use auth::{authorize, parse_loginctl, session_ok, Peer, SessionInfo};
pub use dispatch::dispatch;
pub use keys::discover_keyboards;
pub use platform::{FakePlatform, Platform};
pub use runtime::Runtime;
pub use server::serve;
pub use system::{ac_online, cpu_is_intel, find_nvidia_runtime, profile_command, profile_from_active_file, SysPlatform};
