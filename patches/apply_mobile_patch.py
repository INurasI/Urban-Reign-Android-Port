#!/usr/bin/env python3
from pathlib import Path
import shutil

root = Path(__import__("sys").argv[1]).resolve()
patch_dir = Path(__file__).resolve().parent
target = root / "scripts" / "mobile_touch_controls.gd"
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(patch_dir / "mobile_touch_controls.gd", target)

fight = root / "scenes" / "fight.tscn"
text = fight.read_text()
ext = '[ext_resource type="Script" path="res://scripts/mobile_touch_controls.gd" id="4_mobile"]'
node = '[node name="MobileTouchControls" type="CanvasLayer" parent="."]\nscript = ExtResource("4_mobile")'
if ext not in text:
    anchor = '[ext_resource type="PackedScene" path="res://scenes/ui/hud.tscn" id="3_hud"]'
    if anchor not in text:
        raise SystemExit("Could not find HUD resource line in scenes/fight.tscn")
    text = text.replace(anchor, anchor + "\n" + ext, 1)
if 'name="MobileTouchControls"' not in text:
    text = text.rstrip() + "\n\n" + node + "\n"
fight.write_text(text)

menu = root / "scripts" / "ui" / "main_menu.gd"
text = menu.read_text()
call = '\t_add_base64e_branding()'
if call not in text:
    anchor = '\t_add_wallpaper()'
    if anchor not in text:
        raise SystemExit("Could not find main menu _add_wallpaper() call")
    text = text.replace(anchor, anchor + "\n" + call, 1)

labels = {
    '\tstart_button.pressed.connect(_start.bind(GameState.Mode.VS_CPU))': '\tstart_button.text = "БОЙ С CPU"\n\tstart_button.pressed.connect(_start.bind(GameState.Mode.VS_CPU))',
    '\tarcade_button.pressed.connect(_start.bind(GameState.Mode.ARCADE))': '\tarcade_button.text = "АРКАДА"\n\tarcade_button.pressed.connect(_start.bind(GameState.Mode.ARCADE))',
    '\tversus_button.pressed.connect(_start.bind(GameState.Mode.VERSUS))': '\tversus_button.text = "2 ИГРОКА"\n\tversus_button.pressed.connect(_start.bind(GameState.Mode.VERSUS))',
    '\ttraining_button.pressed.connect(_start.bind(GameState.Mode.TRAINING))': '\ttraining_button.text = "ТРЕНИРОВКА"\n\ttraining_button.pressed.connect(_start.bind(GameState.Mode.TRAINING))',
    '\toptions_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(OPTIONS_SCENE))': '\toptions_button.text = "НАСТРОЙКИ"\n\toptions_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(OPTIONS_SCENE))',
    '\tbest_scores_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(BEST_SCORES_SCENE))': '\tbest_scores_button.text = "РЕКОРДЫ"\n\tbest_scores_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(BEST_SCORES_SCENE))',
    '\tcredits_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(CREDITS_SCENE))': '\tcredits_button.text = "ОБ ИГРЕ"\n\tcredits_button.pressed.connect(func() -> void: get_tree().change_scene_to_file(CREDITS_SCENE))',
    '\tquit_button.pressed.connect(_on_quit_pressed)': '\tquit_button.text = "ВЫХОД"\n\tquit_button.pressed.connect(_on_quit_pressed)',
    '\tdemo_button.text = "Demo"': '\tdemo_button.text = "ДЕМО"',
    '\tonline_button.text = "Online"': '\tonline_button.text = "ОНЛАЙН"',
    '\treplays_button.text = "Replays"': '\treplays_button.text = "ПОВТОРЫ"',
}
for old, new in labels.items():
    if old in text and new not in text:
        text = text.replace(old, new, 1)

if "func _add_base64e_branding()" not in text:
    text += """

## Minimal BASE64E branding for the Android port.
func _add_base64e_branding() -> void:
	var footer := HBoxContainer.new()
	footer.name = "Base64EFooter"
	footer.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	footer.anchor_top = 1.0
	footer.anchor_bottom = 1.0
	footer.offset_left = 28
	footer.offset_top = -62
	footer.offset_right = -28
	footer.offset_bottom = -14
	footer.add_theme_constant_override("separation", 16)
	footer.alignment = BoxContainer.ALIGNMENT_END
	footer.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(footer)

	var credit := Label.new()
	credit.text = "by base64E  ·  @base64E"
	credit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	credit.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	credit.add_theme_font_size_override("font_size", 30)
	credit.add_theme_color_override("font_color", Color(0.78, 0.79, 0.84, 0.92))
	footer.add_child(credit)

	var telegram := Button.new()
	telegram.text = "TELEGRAM  @android_port"
	telegram.custom_minimum_size = Vector2(420, 60)
	telegram.focus_mode = Control.FOCUS_NONE
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.04, 0.06, 0.09, 0.92)
	style.border_color = Color(0.95, 0.72, 0.28, 0.85)
	style.set_border_width_all(1)
	style.set_corner_radius_all(12)
	telegram.add_theme_stylebox_override("normal", style)
	telegram.add_theme_stylebox_override("hover", style)
	telegram.add_theme_stylebox_override("pressed", style)
	telegram.add_theme_color_override("font_color", Color(1.0, 0.88, 0.58))
	telegram.add_theme_font_size_override("font_size", 24)
	footer.add_child(telegram)
	telegram.pressed.connect(func() -> void: OS.shell_open("https://t.me/android_port"))
"""
menu.write_text(text)

# Mobile defaults: prefer the low graphics preset and 720p 3D render cap on fresh installs.
settings = root / "scripts" / "autoload" / "settings.gd"
if settings.exists():
    settings_text = settings.read_text()
    settings_text = settings_text.replace("var resolution := 2", "var resolution := 0")
    settings_text = settings_text.replace("var graphics: Graphics = Graphics.HIGH", "var graphics: Graphics = Graphics.LOW")
    settings.write_text(settings_text)

# The Android overlay is deliberately kept above gameplay, but its own pause handling hides
# it while the scene tree is paused so pause-menu buttons remain touchable.

print("Android touch controls, mobile defaults and BASE64E menu branding patched.")
