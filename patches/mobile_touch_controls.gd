extends CanvasLayer
## Android overlay with per-button position, size and opacity settings.
## Original p1_* actions are reused; preferences persist in user://mobile_controls.cfg.

const ACTION_KEYS := {
	"left": "p1_left", "right": "p1_right", "up": "p1_up", "down": "p1_down",
	"lp": "p1_lp", "hp": "p1_hp", "lk": "p1_lk", "hk": "p1_hk",
	"sidestep": "p1_sidestep"
}
const LABELS := {
	"left": "←", "right": "→", "up": "↑", "down": "↓",
	"lp": "LP", "hp": "HP", "lk": "LK", "hk": "HK",
	"sidestep": "STEP", "pause": "Ⅱ"
}
const DEFAULT_POS := {
	"left": Vector2(0.035, 0.785), "right": Vector2(0.155, 0.785),
	"up": Vector2(0.095, 0.685), "down": Vector2(0.095, 0.885),
	"sidestep": Vector2(0.695, 0.685), "lp": Vector2(0.785, 0.755),
	"hp": Vector2(0.875, 0.685), "lk": Vector2(0.875, 0.835),
	"hk": Vector2(0.785, 0.885), "pause": Vector2(0.935, 0.055)
}
const CONFIG_PATH := "user://mobile_controls.cfg"
const BUTTON_BASE := Vector2(86, 72)
const JOYSTICK_BASE := Vector2(166, 166)
const JOYSTICK_KNOB := Vector2(66, 66)

var _root: Control
var _buttons: Dictionary = {}
var _layout: Dictionary = {}
var _button_scales: Dictionary = {}
var _button_opacities: Dictionary = {}
var _editing := false
var _dragging := ""
var _drag_index := -1
var _panel: PanelContainer
var _selected: OptionButton
var _button_size_slider: HSlider
var _button_opacity_slider: HSlider
var _size_value: Label
var _opacity_value: Label
var _mode_button: OptionButton
var _mode := "joystick"
var _joystick: Panel
var _joystick_knob: Panel
var _joystick_active := false
var _joystick_index := -1
var _joystick_vector := Vector2.ZERO
var _gear: Button

func _ready() -> void:
	if not OS.has_feature("android"):
		queue_free()
		return
	set_process_mode(Node.PROCESS_MODE_ALWAYS)
	layer = 100
	_load_config()
	_root = Control.new()
	_root.name = "TouchOverlay"
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_PASS
	add_child(_root)
	for key: String in LABELS:
		_make_button(key)
	_make_joystick()
	_build_settings()
	_root.resized.connect(_layout_buttons)
	_layout_buttons()
	_apply_style()

func _process(_delta: float) -> void:
	# When paused, hide this high-layer overlay so the original pause menu can receive touches.
	if is_instance_valid(_root):
		_root.visible = not get_tree().paused

func _load_config() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG_PATH) != OK:
		return
	_mode = str(cfg.get_value("controls", "mode", _mode))
	for key: String in DEFAULT_POS:
		var p: Variant = cfg.get_value("layout", key, null)
		if p is Vector2:
			_layout[key] = Vector2(clampf(p.x, 0.0, 1.0), clampf(p.y, 0.0, 1.0))
		_button_scales[key] = clampf(float(cfg.get_value("button_size", key, 1.0)), 0.50, 3.0)
		_button_opacities[key] = clampf(float(cfg.get_value("button_opacity", key, 0.78)), 0.15, 1.0)
	var joystick_pos: Variant = cfg.get_value("layout", "joystick", null)
	if joystick_pos is Vector2:
		_layout["joystick"] = Vector2(clampf(joystick_pos.x, 0.0, 1.0), clampf(joystick_pos.y, 0.0, 1.0))
	_button_scales["joystick"] = clampf(float(cfg.get_value("button_size", "joystick", 1.0)), 0.50, 3.0)
	_button_opacities["joystick"] = clampf(float(cfg.get_value("button_opacity", "joystick", 0.72)), 0.15, 1.0)

func _save_config() -> void:
	var cfg := ConfigFile.new()
	cfg.set_value("controls", "mode", _mode)
	for key: String in _buttons:
		var b := _buttons[key] as Button
		var denom := Vector2(maxf(_root.size.x, 1.0), maxf(_root.size.y, 1.0))
		_layout[key] = Vector2(b.position.x / denom.x, b.position.y / denom.y)
		cfg.set_value("layout", key, _layout[key])
		cfg.set_value("button_size", key, float(_button_scales.get(key, 1.0)))
		cfg.set_value("button_opacity", key, float(_button_opacities.get(key, 0.78)))
	cfg.set_value("layout", "joystick", _layout.get("joystick", Vector2(0.035, 0.76)))
	cfg.set_value("button_size", "joystick", float(_button_scales.get("joystick", 1.0)))
	cfg.set_value("button_opacity", "joystick", float(_button_opacities.get("joystick", 0.72)))
	cfg.save(CONFIG_PATH)

func _make_button(key: String) -> void:
	var b := Button.new()
	b.name = "Touch_" + key
	b.text = LABELS[key]
	b.focus_mode = Control.FOCUS_NONE
	b.mouse_filter = Control.MOUSE_FILTER_STOP
	b.custom_minimum_size = BUTTON_BASE
	b.add_theme_font_size_override("font_size", 22 if key != "sidestep" else 14)
	var normal := StyleBoxFlat.new()
	normal.bg_color = Color(0.035, 0.045, 0.065, 0.94)
	normal.border_color = Color(0.95, 0.72, 0.28, 0.95)
	normal.set_border_width_all(2)
	normal.set_corner_radius_all(18)
	normal.content_margin_left = 6
	normal.content_margin_right = 6
	normal.content_margin_top = 4
	normal.content_margin_bottom = 4
	var pressed := StyleBoxFlat.new()
	pressed.bg_color = Color(0.85, 0.53, 0.16, 0.98)
	pressed.border_color = Color(1.0, 0.88, 0.58, 1.0)
	pressed.set_border_width_all(2)
	pressed.set_corner_radius_all(18)
	b.add_theme_stylebox_override("normal", normal)
	b.add_theme_stylebox_override("hover", normal)
	b.add_theme_stylebox_override("focus", normal)
	b.add_theme_stylebox_override("pressed", pressed)
	b.add_theme_color_override("font_color", Color(1.0, 0.96, 0.86))
	b.add_theme_color_override("font_pressed_color", Color(0.06, 0.05, 0.04))
	_root.add_child(b)
	_buttons[key] = b
	_button_scales[key] = float(_button_scales.get(key, 1.0))
	_button_opacities[key] = float(_button_opacities.get(key, 0.78))
	b.button_down.connect(_on_button_down.bind(key))
	b.button_up.connect(_on_button_up.bind(key))
	b.gui_input.connect(_on_button_gui_input.bind(key))

func _layout_buttons() -> void:
	if _root == null:
		return
	for key: String in _buttons:
		var b := _buttons[key] as Button
		var normalized: Vector2 = _layout.get(key, DEFAULT_POS[key])
		var scale_factor := float(_button_scales.get(key, 1.0))
		b.size = BUTTON_BASE * scale_factor
		b.custom_minimum_size = b.size
		b.position = Vector2(
			clampf(normalized.x * _root.size.x, 0.0, maxf(0.0, _root.size.x - b.size.x)),
			clampf(normalized.y * _root.size.y, 0.0, maxf(0.0, _root.size.y - b.size.y)))
	_update_joystick_layout()
	_apply_visibility()
	_apply_style()

func _apply_visibility() -> void:
	for key: String in ["left", "right", "up", "down"]:
		(_buttons[key] as Button).visible = (_mode == "cross")
	if _joystick:
		_joystick.visible = (_mode == "joystick")

func _apply_style() -> void:
	for key: String in _buttons:
		(_buttons[key] as Button).modulate.a = float(_button_opacities.get(key, 0.78))
	if _joystick:
		_joystick.modulate.a = float(_button_opacities.get("joystick", 0.72))
	if _joystick_knob:
		_joystick_knob.modulate.a = minf(1.0, float(_button_opacities.get("joystick", 0.72)) + 0.18)
	if _panel:
		_panel.modulate.a = 1.0

func _on_button_down(key: String) -> void:
	if _editing:
		return
	if key == "pause":
		var ev := InputEventAction.new()
		ev.action = "pause"
		ev.pressed = true
		Input.parse_input_event(ev)
		ev = InputEventAction.new()
		ev.action = "pause"
		ev.pressed = false
		Input.parse_input_event(ev)
		return
	if ACTION_KEYS.has(key):
		Input.action_press(ACTION_KEYS[key])

func _on_button_up(key: String) -> void:
	if ACTION_KEYS.has(key):
		Input.action_release(ACTION_KEYS[key])

func _on_button_gui_input(event: InputEvent, key: String) -> void:
	if not _editing:
		return
	if event is InputEventScreenTouch and event.pressed:
		_dragging = key
		_drag_index = event.index
		get_viewport().set_input_as_handled()
	elif event is InputEventScreenDrag and _dragging == key and event.index == _drag_index:
		var b := _buttons[key] as Button
		b.position += event.relative
		b.position.x = clampf(b.position.x, 0.0, maxf(0.0, _root.size.x - b.size.x))
		b.position.y = clampf(b.position.y, 0.0, maxf(0.0, _root.size.y - b.size.y))
		get_viewport().set_input_as_handled()
	elif event is InputEventScreenTouch and not event.pressed and _dragging == key and event.index == _drag_index:
		_dragging = ""
		_drag_index = -1
		_save_config()
		get_viewport().set_input_as_handled()

func _make_joystick() -> void:
	_joystick = Panel.new()
	_joystick.name = "RoundMovementJoystick"
	_joystick.mouse_filter = Control.MOUSE_FILTER_STOP
	_joystick.size = JOYSTICK_BASE
	var base_style := StyleBoxFlat.new()
	base_style.bg_color = Color(0.035, 0.045, 0.065, 0.55)
	base_style.border_color = Color(0.95, 0.72, 0.28, 0.82)
	base_style.set_border_width_all(3)
	base_style.set_corner_radius_all(100)
	_joystick.add_theme_stylebox_override("panel", base_style)
	_root.add_child(_joystick)
	_joystick_knob = Panel.new()
	_joystick_knob.name = "JoystickKnob"
	_joystick_knob.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_joystick_knob.size = JOYSTICK_KNOB
	var knob_style := StyleBoxFlat.new()
	knob_style.bg_color = Color(0.95, 0.72, 0.28, 0.86)
	knob_style.border_color = Color(1.0, 0.9, 0.62, 0.95)
	knob_style.set_border_width_all(2)
	knob_style.set_corner_radius_all(100)
	_joystick_knob.add_theme_stylebox_override("panel", knob_style)
	_joystick.add_child(_joystick_knob)
	_joystick.gui_input.connect(_on_joystick_input)
	_update_joystick_layout()

func _update_joystick_layout() -> void:
	if not is_instance_valid(_joystick) or _root == null:
		return
	var pos: Vector2 = _layout.get("joystick", Vector2(0.035, 0.76))
	_joystick.size = JOYSTICK_BASE * float(_button_scales.get("joystick", 1.0))
	_joystick.position = Vector2(
		clampf(pos.x * _root.size.x, 0.0, maxf(0.0, _root.size.x - _joystick.size.x)),
		clampf(pos.y * _root.size.y, 0.0, maxf(0.0, _root.size.y - _joystick.size.y)))
	var knob_size := JOYSTICK_KNOB * float(_button_scales.get("joystick", 1.0))
	_joystick_knob.size = knob_size
	_joystick_knob.position = (_joystick.size - knob_size) * 0.5
	if _joystick_active:
		_update_joystick_vector(_joystick_vector)

func _on_joystick_input(event: InputEvent) -> void:
	if _editing:
		if event is InputEventScreenTouch and event.pressed:
			_dragging = "joystick"
			_drag_index = event.index
		elif event is InputEventScreenDrag and _dragging == "joystick" and event.index == _drag_index:
			_joystick.position += event.relative
			_joystick.position.x = clampf(_joystick.position.x, 0.0, maxf(0.0, _root.size.x - _joystick.size.x))
			_joystick.position.y = clampf(_joystick.position.y, 0.0, maxf(0.0, _root.size.y - _joystick.size.y))
		elif event is InputEventScreenTouch and not event.pressed and _dragging == "joystick":
			_dragging = ""
			_drag_index = -1
			_save_joystick_position()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventScreenTouch:
		if event.pressed:
			_joystick_active = true
			_joystick_index = event.index
			_update_joystick_vector(event.position)
		elif event.index == _joystick_index:
			_release_joystick()
	elif event is InputEventScreenDrag and _joystick_active and event.index == _joystick_index:
		_update_joystick_vector(event.position)
	get_viewport().set_input_as_handled()

func _update_joystick_vector(local_pos: Vector2) -> void:
	var center := _joystick.size * 0.5
	var delta := (local_pos - center) / maxf(_joystick.size.x * 0.36, 1.0)
	_joystick_vector = delta.limit_length(1.0)
	var knob_offset := _joystick_vector * ((_joystick.size - _joystick_knob.size).x * 0.5)
	_joystick_knob.position = (_joystick.size - _joystick_knob.size) * 0.5 + knob_offset
	_set_move_action("left", _joystick_vector.x < -0.28)
	_set_move_action("right", _joystick_vector.x > 0.28)
	_set_move_action("up", _joystick_vector.y < -0.28)
	_set_move_action("down", _joystick_vector.y > 0.28)

func _set_move_action(key: String, pressed: bool) -> void:
	var action: String = ACTION_KEYS[key]
	if pressed and not Input.is_action_pressed(action):
		Input.action_press(action)
	elif not pressed and Input.is_action_pressed(action):
		Input.action_release(action)

func _release_joystick() -> void:
	_joystick_active = false
	_joystick_index = -1
	_joystick_vector = Vector2.ZERO
	for key: String in ["left", "right", "up", "down"]:
		Input.action_release(ACTION_KEYS[key])
	_joystick_knob.position = (_joystick.size - _joystick_knob.size) * 0.5

func _save_joystick_position() -> void:
	_layout["joystick"] = Vector2(_joystick.position.x / maxf(_root.size.x, 1.0), _joystick.position.y / maxf(_root.size.y, 1.0))
	_save_config()

func _build_settings() -> void:
	_gear = Button.new()
	_gear.name = "TouchSettingsButton"
	_gear.text = "⚙"
	_gear.position = Vector2(20, 18)
	_gear.size = Vector2(64, 56)
	_gear.custom_minimum_size = _gear.size
	_gear.focus_mode = Control.FOCUS_NONE
	_gear.add_theme_font_size_override("font_size", 28)
	var gear_style := StyleBoxFlat.new()
	gear_style.bg_color = Color(0.035, 0.045, 0.065, 0.88)
	gear_style.border_color = Color(0.95, 0.72, 0.28, 0.9)
	gear_style.set_border_width_all(2)
	gear_style.set_corner_radius_all(14)
	_gear.add_theme_stylebox_override("normal", gear_style)
	_gear.add_theme_stylebox_override("hover", gear_style)
	_gear.add_theme_stylebox_override("pressed", gear_style)
	_root.add_child(_gear)
	_gear.pressed.connect(_toggle_settings)

	_panel = PanelContainer.new()
	_panel.name = "ControlSettings"
	_panel.visible = false
	_panel.z_index = 10
	_panel.custom_minimum_size = Vector2(460, 0)
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.035, 0.045, 0.065, 0.98)
	panel_style.border_color = Color(0.95, 0.72, 0.28, 0.95)
	panel_style.set_border_width_all(2)
	panel_style.set_corner_radius_all(18)
	panel_style.content_margin_left = 20
	panel_style.content_margin_right = 20
	panel_style.content_margin_top = 16
	panel_style.content_margin_bottom = 16
	_panel.add_theme_stylebox_override("panel", panel_style)
	_root.add_child(_panel)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(420, 370)
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_panel.add_child(scroll)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 9)
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	var title := Label.new()
	title.text = "УПРАВЛЕНИЕ"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 24)
	title.add_theme_color_override("font_color", Color(1.0, 0.82, 0.4))
	column.add_child(title)

	var mode_label := Label.new()
	mode_label.text = "Движение"
	column.add_child(mode_label)
	_mode_button = OptionButton.new()
	_mode_button.add_item("Крестик", 0)
	_mode_button.add_item("Круглый джойстик", 1)
	_mode_button.select(0 if _mode == "cross" else 1)
	column.add_child(_mode_button)
	_mode_button.item_selected.connect(func(index: int) -> void:
		_mode = "joystick" if index == 1 else "cross"
		_apply_visibility()
		_save_config())

	var select_label := Label.new()
	select_label.text = "Настраиваемая кнопка"
	column.add_child(select_label)
	_selected = OptionButton.new()
	for key: String in LABELS:
		_selected.add_item("Пауза" if key == "pause" else ("Движение: " + LABELS[key] if ["left", "right", "up", "down"].has(key) else LABELS[key]), _selected.get_item_count())
	_selected.add_item("Круглый джойстик", _selected.get_item_count())
	column.add_child(_selected)
	_selected.item_selected.connect(_on_selected_button_changed)

	_add_button_slider(column, "Размер выбранной кнопки", 0.50, 3.0, "size")
	_add_button_slider(column, "Прозрачность выбранной кнопки", 0.15, 1.0, "opacity")
	var edit := Button.new()
	edit.text = "ПЕРЕМЕЩАТЬ КНОПКИ: ВЫКЛ"
	edit.custom_minimum_size.y = 44
	column.add_child(edit)
	edit.pressed.connect(func() -> void:
		_editing = not _editing
		edit.text = "ПЕРЕМЕЩАТЬ КНОПКИ: " + ("ВКЛ" if _editing else "ВЫКЛ")
		edit.modulate = Color(1.0, 0.82, 0.4) if _editing else Color.WHITE)
	var footer := HBoxContainer.new()
	column.add_child(footer)
	var reset := Button.new()
	reset.text = "СБРОСИТЬ"
	reset.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	footer.add_child(reset)
	reset.pressed.connect(_reset_layout)
	var close := Button.new()
	close.text = "ГОТОВО"
	close.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	footer.add_child(close)
	close.pressed.connect(_toggle_settings)
	_refresh_selected_sliders()

func _add_button_slider(parent: VBoxContainer, caption: String, minimum: float, maximum: float, kind: String) -> void:
	var line := VBoxContainer.new()
	line.add_theme_constant_override("separation", 3)
	parent.add_child(line)
	var heading := HBoxContainer.new()
	line.add_child(heading)
	var label := Label.new()
	label.text = caption
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(label)
	var value_label := Label.new()
	value_label.custom_minimum_size.x = 48
	value_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	heading.add_child(value_label)
	var slider := HSlider.new()
	slider.min_value = minimum
	slider.max_value = maximum
	slider.step = 0.05
	slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.add_child(slider)
	if kind == "size":
		_button_size_slider = slider
		_size_value = value_label
	else:
		_button_opacity_slider = slider
		_opacity_value = value_label
	slider.value_changed.connect(func(value: float) -> void:
		var key := _selected_key()
		if kind == "size":
			_button_scales[key] = value
		else:
			_button_opacities[key] = value
		_layout_buttons()
		_refresh_slider_labels()
		_save_config())

func _selected_key() -> String:
	var index := _selected.selected
	if index >= LABELS.size():
		return "joystick"
	return str(LABELS.keys()[maxi(0, index)])

func _on_selected_button_changed(_index: int) -> void:
	_refresh_selected_sliders()

func _refresh_selected_sliders() -> void:
	if not is_instance_valid(_selected) or not is_instance_valid(_button_size_slider):
		return
	var key := _selected_key()
	_button_size_slider.set_value_no_signal(float(_button_scales.get(key, 1.0)))
	_button_opacity_slider.set_value_no_signal(float(_button_opacities.get(key, 0.72)))
	_refresh_slider_labels()

func _refresh_slider_labels() -> void:
	if _size_value:
		_size_value.text = "%d%%" % roundi(_button_size_slider.value * 100.0)
	if _opacity_value:
		_opacity_value.text = "%d%%" % roundi(_button_opacity_slider.value * 100.0)

func _toggle_settings() -> void:
	_panel.visible = not _panel.visible
	if _panel.visible:
		_panel.size = _panel.get_combined_minimum_size()
		_panel.position = (_root.size - _panel.size) * 0.5
		_panel.position.x = maxf(8.0, _panel.position.x)
		_panel.position.y = maxf(8.0, _panel.position.y)
		_panel.move_to_front()

func _reset_layout() -> void:
	_layout.clear()
	_button_scales.clear()
	_button_opacities.clear()
	for key: String in LABELS:
		_button_scales[key] = 1.0
		_button_opacities[key] = 0.78
	_button_scales["joystick"] = 1.0
	_button_opacities["joystick"] = 0.72
	_mode = "joystick"
	_mode_button.select(1)
	_editing = false
	_layout_buttons()
	_refresh_selected_sliders()
	_save_config()
