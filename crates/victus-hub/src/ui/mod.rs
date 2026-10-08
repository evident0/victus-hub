//! GTK 4 / libadwaita panel. The page layout follows the previous Qt UI.
//!
//! This glib's `SourceId` does not remove its callback when dropped. Timers
//! still forget the id so a later `Drop` cannot cancel them.

#![allow(clippy::cognitive_complexity, clippy::similar_names, clippy::cast_possible_wrap, clippy::wildcard_imports)]
#![allow(clippy::items_after_statements, clippy::too_many_lines, clippy::option_if_let_else)]

pub mod graph;
pub mod pages;
pub mod paint;
pub mod tray;
pub mod widgets;
mod actions;
mod controls;
mod curve;
mod host;
mod keyboard;
mod labels;
mod maintenance;
mod readings;
mod session;
mod shell;
mod view;
mod wiring;

pub use host::{frequency_window, gpu_name};
pub use shell::{hydrate_live, start};

#[cfg(test)]
#[path = "../../../../tests/rust/victus-hub/ui/mod.rs"]
mod regression_tests;
