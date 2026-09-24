"""D-Bus cold start and existing-window activation on an isolated bus."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from victus_hub.app.activation import notify_existing_instance


class TestActivation(unittest.TestCase):
    def test_terminal_message(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        terminal = Terminal()
        notify_existing_instance(terminal)
        self.assertIn("already running", terminal.getvalue())
        self.assertIn("quit it from the tray", terminal.getvalue())
        non_terminal = io.StringIO()
        notify_existing_instance(non_terminal)
        self.assertEqual(non_terminal.getvalue(), "")

    @unittest.skipUnless(shutil.which("dbus-run-session") and shutil.which("gdbus"),
                         "D-Bus tools are required")
    def test_cold_start_and_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "stub.py"
            stub.write_text('''
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtDBus import QDBusConnection
from victus_hub.app.activation import BUS_NAME, OBJECT_PATH, ApplicationService

app = QCoreApplication([])
bus = QDBusConnection.sessionBus()
service = ApplicationService()
assert bus.registerObject(OBJECT_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots)
assert bus.registerService(BUS_NAME)
count = 0
def activated():
    global count
    count += 1
    Path(sys.argv[1]).write_text(str(count))
    if count == 2:
        app.quit()
service.activate_requested.connect(activated)
service.flush_pending()
QTimer.singleShot(5000, app.quit)
app.exec()
''')
            services = root / "dbus-1" / "services"
            services.mkdir(parents=True)
            count = root / "count"
            (services / "io.github.evident0.VictusHub.service").write_text(
                "[D-BUS Service]\nName=io.github.evident0.VictusHub\n"
                f"Exec={sys.executable} {stub} {count}\n"
            )
            driver = '''
import subprocess
import xml.etree.ElementTree as ET
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection
from victus_hub.app.activation import BUS_NAME, OBJECT_PATH, request_activation

cmd = ["gdbus", "call", "--session", "--dest", BUS_NAME, "--object-path", OBJECT_PATH,
       "--method", "org.freedesktop.Application.Activate", "{}"]
assert subprocess.run(cmd, capture_output=True).returncode == 0
xml = subprocess.check_output(["gdbus", "introspect", "--xml", "--session",
                               "--dest", BUS_NAME, "--object-path", OBJECT_PATH])
interface = next(i for i in ET.fromstring(xml).findall("interface")
                 if i.attrib["name"] == "org.freedesktop.Application")
methods = {m.attrib["name"]: [a.attrib["type"] for a in m.findall("arg")]
           for m in interface.findall("method")}
assert methods["Activate"] == ["a{sv}"]
assert methods["Open"] == ["as", "a{sv}"]
assert methods["ActivateAction"] == ["s", "av", "a{sv}"]
app = QCoreApplication([])
bus = QDBusConnection.sessionBus()
assert not bus.registerService(BUS_NAME)
assert request_activation(bus)
'''
            env = dict(os.environ, XDG_DATA_HOME=directory, PYTHONPATH=os.getcwd(),
                       QT_QPA_PLATFORM="offscreen")
            result = subprocess.run(["dbus-run-session", "--", sys.executable, "-c", driver],
                                    env=env, capture_output=True, text=True, timeout=9)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(count.read_text(), "2")
