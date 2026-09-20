"""Regression tests for runtime ownership of SettingsRow labels."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from victus_hub.features.gpu.mux import GpuMuxMode, GpuMuxState
from victus_hub.widgets.chrome import SettingsRow
from victus_hub.widgets.profile_section import ProfileSection


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

    def test_mux_segment_is_parented_during_construction(self):
        state = GpuMuxState(
            modes=(GpuMuxMode("hybrid", 0, "Hybrid"), GpuMuxMode("discrete", 1, "Discrete")),
            current_index=0,
        )
        with patch("victus_hub.widgets.profile_section.read_gpu_mux_state", return_value=state):
            section = ProfileSection()
        self.addCleanup(section.deleteLater)

        self.assertNotIn(
            section._mux_seg,
            [widget for widget in self.app.topLevelWidgets() if widget is not section],
        )
        self.assertIs(section._mux_seg.parentWidget(), section._mux_block)


if __name__ == "__main__":
    unittest.main()
