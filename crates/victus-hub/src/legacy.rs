//! Read the Qt INI settings used before the Rust port. No Qt runtime is needed.

pub use victus_core::{migrated_state, settings};

#[cfg(test)]
#[path = "../../../tests/rust/victus-hub/legacy.rs"]
mod tests;
