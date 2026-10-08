//! Session-bus name used by `scripts/stop-gui` and program-shortcut activation.
//!
//! Nothing in the library tests connects to a bus. The binary calls
//! [`own_session_name`] only when the panel is not in offline preview.

use std::collections::HashMap;
use std::sync::mpsc::Sender;

use zbus::zvariant::OwnedValue;
use zbus::interface;

const BUS_NAME: &str = "io.github.evident0.VictusHub";
const OBJECT_PATH: &str = "/io/github/evident0/VictusHub";

struct Application {
    show: Sender<()>,
}

#[interface(name = "org.freedesktop.Application")]
impl Application {
    fn activate(&mut self, platform_data: HashMap<String, OwnedValue>) {
        let _ = platform_data;
        let _ = self.show.send(());
    }
}

/// Own the well-known name until the thread is stopped. `stop-gui` looks up
/// this name and signals the process. `Activate` raises the window.
pub fn own_session_name(show: Sender<()>) -> Result<(), String> {
    let connection = zbus::blocking::Connection::session().map_err(|error| error.to_string())?;
    connection
        .object_server()
        .at(OBJECT_PATH, Application { show })
        .map_err(|error| error.to_string())?;
    connection.request_name(BUS_NAME).map_err(|error| error.to_string())?;
    std::thread::park();
    Ok(())
}
