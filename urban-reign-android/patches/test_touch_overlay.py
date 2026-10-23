#!/usr/bin/env python3
"""Regression checks for Android touch control sizing and positioning.

These are source-level guards, not a device-rendering test. A successful run must
never be interpreted as proof that the Android NativeActivity surface composites
the overlay visibly on a physical device.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
JAVA = (
    ROOT
    / "urban-reign-android/android-app/app/src/main/java/"
    / "kz/nurasql77/urbanreign/MainActivity.java"
).read_text(encoding="utf-8")


class TouchOverlayTests(unittest.TestCase):
    def test_every_overlay_child_is_added_with_explicit_dimensions(self):
        self.assertIn("touchOverlay.addView(control.view, params);", JAVA)
        self.assertIn("touchOverlay.addView(stick, stickParams);", JAVA)
        self.assertIn("touchOverlay.addView(gear, gearParams);", JAVA)
        self.assertNotIn("touchOverlay.addView(control.view);", JAVA)
        self.assertNotIn("touchOverlay.addView(stick);", JAVA)
        self.assertNotIn("touchOverlay.addView(gear);", JAVA)

    def test_child_size_is_not_dependent_on_first_parent_resize(self):
        self.assertIn("protected void onLayout(boolean changed, int left, int top, int right, int bottom)", JAVA)
        self.assertIn("super.onLayout(changed, left, top, right, bottom);", JAVA)
        self.assertIn("layoutControls();", JAVA)
        self.assertIn('private int controlBaseSizeDp(String key)', JAVA)

    def test_normal_layout_clamps_controls_inside_overlay(self):
        start = JAVA.index("private void positionView(")
        end = JAVA.index("private final class TouchOverlay", start)
        position_code = JAVA[start:end]
        self.assertIn("Math.max(0, Math.min(Math.max(0, w - size)", position_code)
        self.assertIn("Math.max(0, Math.min(Math.max(0, h - size)", position_code)
        self.assertIn("Gravity.TOP | Gravity.LEFT", position_code)

    def test_controls_have_visible_label_styling(self):
        self.assertIn("b.setTextColor(Color.WHITE);", JAVA)
        self.assertIn("b.setBackground(background());", JAVA)
        self.assertIn("touchOverlay.setBackgroundColor(Color.TRANSPARENT);", JAVA)
        self.assertIn("touchOverlay.setElevation(dp(24));", JAVA)
        self.assertIn("Touch overlay laid out:", JAVA)

    def test_gameplay_controls_forward_touch_to_native(self):
        self.assertIn("nativeTouchButton(buttonIndex, true)", JAVA)
        self.assertIn("nativeTouchButton(buttonIndex, false)", JAVA)
        self.assertIn("nativeTouchAxis(0, dx)", JAVA)
        self.assertIn("nativeTouchAxis(1, dy)", JAVA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
