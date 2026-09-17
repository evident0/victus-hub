"""Regression tests for runtime ownership of SettingsRow labels."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from victus_hub.widgets.chrome import SettingsRow


class TestSettingsRow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_labels_are_not_toplevel_widgets_during_construction(self):
        row = SettingsRow("Title", "Subtitle")
        self.addCleanup(row.deleteLater)

        self.assertIs(row._sub.parentWidget(), row)
        self.assertIs(row.layout().itemAt(0).layout().itemAt(1).widget(), row._sub)
        self.assertFalse(row._sub.isWindow())
        self.assertNotIn(row._sub, self.app.topLevelWidgets())


if __name__ == "__main__":
    unittest.main()
