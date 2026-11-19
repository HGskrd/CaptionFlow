//! Focus layout: the window shows only captions. Moving the pointer reveals one
//! control bar at the bottom; its menus hold everything the inspector used to.
use super::controls::{menu_item, overlay};
use super::{CaptionFlow, Palette, string};
use gpui::{
    Context, Corner, Div, ExternalPaths, FontWeight, MouseButton, SharedString, Stateful, Window,
    div, prelude::*, px, relative, rgb, white,
};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

const REVEAL: Duration = Duration::from_millis(2500);
const STYLE: usize = 0;
const LANGUAGES: usize = 1;
const MODELS: usize = 2;

fn heading(text: impl Into<SharedString>, p: Palette) -> Div {
    div()
        .px(px(10.))
        .pt(px(7.))
        .pb(px(3.))
        .text_size(px(11.))
        .font_weight(FontWeight::SEMIBOLD)
        .text_color(p.muted)
        .child(text.into())
}

fn separator(p: Palette) -> Div {
    div().h(px(1.)).mx(px(8.)).my(px(5.)).bg(p.line)
}

fn note(text: impl Into<SharedString>, p: Palette) -> Div {
    div()
        .px(px(10.))
        .pt(px(2.))
        .pb(px(6.))
        .text_size(px(11.))
        .text_color(p.muted)
        .child(text.into())
}

impl CaptionFlow {
    pub(super) fn controls_visible(&self) -> bool {
        !self.flag("active")
            || self.pin_controls
            || self.menu.is_some()
            || self.settings_open
            || self.bar_hover
            || self.controls_until.is_some_and(|t| Instant::now() < t)
    }

    pub(super) fn reveal_controls(&mut self, cx: &mut Context<Self>) {
        let was = self.controls_visible();
        self.controls_until = Some(Instant::now() + REVEAL);
        if !was {
            cx.notify();
        }
    }

    fn toggle_menu(&mut self, name: &str, cx: &mut Context<Self>) {
        if let Some((closed, at)) = &self.menu_closed
            && closed == name
            && at.elapsed() < Duration::from_millis(250)
        {
            self.menu_closed = None;
            return;
        }
        self.picker = None;
        self.menu = if self.menu.as_deref() == Some(name) {
            None
        } else {
            Some(name.to_owned())
        };
        cx.notify();
    }

    pub(super) fn open_menu(&mut self, name: &str, cx: &mut Context<Self>) {
        self.picker = None;
        self.settings_open = false;
        self.menu = Some(name.to_owned());
        cx.notify();
    }

    pub(super) fn open_settings(&mut self, cx: &mut Context<Self>) {
        self.menu = None;
        self.picker = None;
        self.settings_open = true;
        cx.notify();
    }

    fn sections(&self, tab: usize) -> Vec<Value> {
        self.state["sections"][tab]
            .as_array()
            .cloned()
            .unwrap_or_default()
    }

    fn find_field(&self, tab: usize, keys: &[&str]) -> Option<Value> {
        self.sections(tab)
            .iter()
            .flat_map(|s| s["fields"].as_array().cloned().unwrap_or_default())
            .find(|f| keys.contains(&f["key"].as_str().unwrap_or("")))
    }

    fn selected_label(field: &Value) -> String {
        field["options"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|o| o["value"] == field["value"])
            .map(|o| string(&o["label"]))
            .unwrap_or_else(|| string(&field["value"]))
    }

    fn toggle_target(&mut self, language: &str, checked: bool, cx: &mut Context<Self>) {
        let mut targets = self.state["preferences"]["targets"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        if checked {
            targets.retain(|v| v.as_str() != Some(language));
        } else {
            targets.push(json!(language));
        }
        self.command(json!({"type":"set","key":"targets","value":targets}), cx);
        self.menu = Some("translate".into());
    }

    /// Commands close any open pop-up; bar menus stay open so several changes
    /// can be made in a row.
    fn set_in_menu(&mut self, key: &str, value: Value, cx: &mut Context<Self>) {
        let menu = self.menu.clone();
        self.command(json!({"type":"set","key":key,"value":value}), cx);
        self.menu = menu;
    }

    fn panel(&self, name: &str, width: f32, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let name = name.to_owned();
        div()
            .id(SharedString::from(format!("bar-menu-{name}")))
            .occlude()
            .w(px(width))
            .max_h(px(560.))
            .overflow_y_scroll()
            .p(px(5.))
            .rounded(px(12.))
            .bg(p.menu)
            .border_1()
            .border_color(p.menu_line)
            .shadow_lg()
            .text_size(px(13.))
            .text_color(p.text)
            .on_mouse_down_out(cx.listener(move |this, _, _, cx| {
                if this.menu.as_deref() == Some(name.as_str()) {
                    this.menu = None;
                    this.picker = None;
                    this.menu_closed = Some((name.clone(), Instant::now()));
                    cx.notify();
                }
            }))
    }

    /// A list of choices with check marks, used for audio and spoken language.
    fn choice_items(
        &self,
        field: &Value,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Vec<Stateful<Div>> {
        let key = field["key"].as_str().unwrap_or("").to_owned();
        let disabled = field["disabled"].as_bool().unwrap_or(false);
        field["options"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .enumerate()
            .map(|(i, option)| {
                let chosen = option["value"].clone();
                let key = key.clone();
                menu_item(
                    SharedString::from(format!("choice-{key}-{i}")),
                    chosen == field["value"],
                    string(&option["label"]),
                    None,
                    disabled,
                    p,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    if !disabled {
                        this.set_in_menu(&key, chosen.clone(), cx)
                    }
                }))
            })
            .collect()
    }

    fn menu_lock(&self, tab: usize, p: Palette, cx: &mut Context<Self>) -> Option<Div> {
        self.tab_locked(tab)
            .then(|| div().p(px(4.)).child(self.lock_banner(p, cx)))
    }

    fn listening_menu(&self, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let audio = self.find_field(LANGUAGES, &["source", "input"]);
        let spoken = self.find_field(LANGUAGES, &["sourceLanguage"]);
        let audio_note = self
            .sections(LANGUAGES)
            .first()
            .and_then(|s| s["note"].as_str().map(str::to_owned))
            .unwrap_or_default();
        let status = self.state["inputStatus"].as_str().unwrap_or("").to_owned();
        self.panel("listening", 280., p, cx)
            .children(self.menu_lock(LANGUAGES, p, cx))
            .when_some(audio, |el, field| {
                el.child(heading(string(&field["label"]), p))
                    .children(self.choice_items(&field, p, cx))
                    .when(!audio_note.is_empty(), |el| {
                        el.child(note(audio_note.clone(), p))
                    })
            })
            .when_some(spoken, |el, field| {
                el.child(separator(p))
                    .child(heading(string(&field["label"]), p))
                    .children(self.choice_items(&field, p, cx))
            })
            .when(!status.is_empty(), |el| {
                el.child(separator(p)).child(note(status, p))
            })
    }

    fn translate_menu(&self, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let show = self.find_field(LANGUAGES, &["displayMode"]);
        let section = self
            .sections(LANGUAGES)
            .into_iter()
            .find(|s| {
                s["fields"]
                    .as_array()
                    .is_some_and(|f| f.iter().any(|f| f["kind"] == "target"))
            })
            .unwrap_or_default();
        let fields = section["fields"].as_array().cloned().unwrap_or_default();
        self.panel("translate", 300., p, cx)
            .children(self.menu_lock(LANGUAGES, p, cx))
            .when_some(show, |el, field| el.child(self.field_row(&field, p, cx)))
            .child(separator(p))
            .child(heading(string(&section["title"]), p))
            .children(fields.iter().enumerate().map(|(i, field)| {
                let key = field["key"].as_str().unwrap_or("").to_owned();
                let disabled = field["disabled"].as_bool().unwrap_or(false);
                if field["kind"] == "target" {
                    let checked = field["value"].as_bool().unwrap_or(false);
                    menu_item(
                        ("target", i),
                        checked,
                        string(&field["label"]),
                        None,
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.toggle_target(&key, checked, cx)
                        }
                    }))
                } else {
                    menu_item(
                        ("target", i),
                        false,
                        string(&field["label"]),
                        None,
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.command(json!({"type":"edit","key":key}), cx)
                        }
                    }))
                }
            }))
            .when(
                section["note"].as_str().is_some_and(|n| !n.is_empty()),
                |el| el.child(note(string(&section["note"]), p)),
            )
    }

    fn style_menu(&self, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let panel = self.panel("style", 340., p, cx);
        // A pop-up inside this menu opens in place: GPUI cannot float a menu over a menu.
        if let Some(key) = self.picker.clone()
            && let Some(field) = self.find_field(STYLE, &[key.as_str()])
        {
            let label = string(&field["label"]);
            let options = field["options"].as_array().cloned().unwrap_or_default();
            return panel
                .child(
                    menu_item("style-back", false, format!("‹  {label}"), None, false, p)
                        .font_weight(FontWeight::SEMIBOLD)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.picker = None;
                            cx.notify();
                        })),
                )
                .child(separator(p))
                .children(options.into_iter().enumerate().map(|(i, option)| {
                    let chosen = option["value"].clone();
                    let key = key.clone();
                    menu_item(
                        ("style-option", i),
                        chosen == field["value"],
                        string(&option["label"]),
                        None,
                        false,
                        p,
                    )
                    .on_click(
                        cx.listener(move |this, _, _, cx| {
                            this.set_in_menu(&key, chosen.clone(), cx)
                        }),
                    )
                }));
        }
        let sections = self.sections(STYLE);
        let count = sections.len();
        panel.children(sections.into_iter().enumerate().map(|(i, section)| {
            div()
                .when(i > 0, |el| el.child(separator(p)))
                .child(heading(string(&section["title"]), p))
                .children(
                    section["fields"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(|field| self.field_row(field, p, cx)),
                )
                .when(i + 1 == count, |el| el.pb(px(4.)))
        }))
    }

    fn more_menu(&self, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let update = match self.update_status.phase.as_str() {
            "available" => "Download update",
            "ready" => "Restart to update",
            "checking" => "Checking for updates…",
            "downloading" => "Downloading update…",
            _ => "Check for updates",
        };
        let update_disabled = !self.updates.online()
            || matches!(
                self.update_status.phase.as_str(),
                "checking" | "downloading"
            );
        let active = self.flag("active");
        let shortcut = |s: &str| Some(s.to_owned());
        self.panel("more", 300., p, cx)
            .child(
                menu_item(
                    "more-copy",
                    false,
                    if self.copied {
                        "Copied"
                    } else {
                        "Copy captions"
                    },
                    shortcut("⇧⌘C"),
                    false,
                    p,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.copy(cx);
                    this.menu = None;
                })),
            )
            .child(
                menu_item("more-live", false, "Back to live", shortcut("⌘↓"), false, p).on_click(
                    cx.listener(|this, _, _, cx| {
                        this.menu = None;
                        this.back_to_live(None, cx)
                    }),
                ),
            )
            .child(
                menu_item(
                    "more-preview",
                    false,
                    "Preview appearance",
                    shortcut("⇧⌘P"),
                    active,
                    p,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    if !active {
                        this.command(json!({"type":"preview"}), cx);
                        this.menu = None;
                    }
                })),
            )
            .child(separator(p))
            .child(
                menu_item(
                    "more-full",
                    false,
                    "Enter or exit full screen",
                    shortcut("⌃⌘F"),
                    false,
                    p,
                )
                .on_click(cx.listener(|this, _, window, cx| {
                    this.menu = None;
                    window.toggle_fullscreen();
                    cx.notify();
                })),
            )
            .child(
                menu_item(
                    "more-pin",
                    self.pin_controls,
                    "Keep controls visible",
                    shortcut("⌥⌘I"),
                    false,
                    p,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    this.pin_controls = !this.pin_controls;
                    cx.notify();
                })),
            )
            .child(separator(p))
            .child(
                menu_item(
                    "more-settings",
                    false,
                    "Models & Settings…",
                    shortcut("⌘,"),
                    false,
                    p,
                )
                .on_click(cx.listener(|this, _, _, cx| this.open_settings(cx))),
            )
            .child(
                menu_item(
                    "more-privacy",
                    false,
                    "Privacy & Sharing…",
                    None,
                    self.flag("installing"),
                    p,
                )
                .on_click(cx.listener(|this, _, _, cx| {
                    if !this.flag("installing") {
                        this.menu = None;
                        this.command(json!({"type":"privacy"}), cx);
                    }
                })),
            )
            .child(
                menu_item("more-update", false, update, None, update_disabled, p).on_click(
                    cx.listener(move |this, _, _, cx| {
                        if !update_disabled {
                            this.update_app(cx)
                        }
                    }),
                ),
            )
    }

    fn bar_button(&self, name: &str, label: impl Into<SharedString>, p: Palette) -> Stateful<Div> {
        let open = self.menu.as_deref() == Some(name);
        div()
            .id(SharedString::from(format!("bar-{name}")))
            .h(px(36.))
            .px(px(12.))
            .flex()
            .items_center()
            .gap(px(8.))
            .rounded(px(11.))
            .font_weight(FontWeight::MEDIUM)
            .whitespace_nowrap()
            .cursor_pointer()
            .when(open, |el| el.bg(p.control_on))
            .hover(move |s| s.bg(p.control_on))
            .child(label.into())
    }

    fn bar_menu_anchor(
        &self,
        name: &'static str,
        label: impl Into<SharedString>,
        right: bool,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Div {
        let open = self.menu.as_deref() == Some(name);
        div()
            .relative()
            .child(
                self.bar_button(name, label, p)
                    .on_click(cx.listener(move |this, _, _, cx| this.toggle_menu(name, cx))),
            )
            .when(open, |el| {
                let menu = match name {
                    "listening" => self.listening_menu(p, cx),
                    "translate" => self.translate_menu(p, cx),
                    "style" => self.style_menu(p, cx),
                    _ => self.more_menu(p, cx),
                };
                el.child(
                    div()
                        .absolute()
                        .top_0()
                        .when(right, |el| el.right_0())
                        .when(!right, |el| el.left_0())
                        .child(overlay(
                            if right {
                                Corner::BottomRight
                            } else {
                                Corner::BottomLeft
                            },
                            -10.,
                            menu,
                        )),
                )
            })
    }

    fn bar_separator(p: Palette) -> Div {
        div().w(px(1.)).h(px(20.)).mx(px(3.)).bg(p.line)
    }

    pub(super) fn control_bar(&self, p: Palette, cx: &mut Context<Self>) -> Stateful<Div> {
        let active = self.flag("active");
        let locked = self.flag("stopping") || self.flag("installing");
        let seconds = self.state["capturedSeconds"].as_f64().unwrap_or(0.) as u64;
        let listen_label = if self.flag("stopping") {
            "Stopping…".to_owned()
        } else if active && seconds > 0 {
            format!("Stop   {:02}:{:02}", seconds / 60, seconds % 60)
        } else if active {
            "Stop".to_owned()
        } else {
            "Start listening".to_owned()
        };
        let audio = self
            .find_field(LANGUAGES, &["source", "input"])
            .map(|f| Self::selected_label(&f))
            .unwrap_or_default();
        let spoken = self
            .find_field(LANGUAGES, &["sourceLanguage"])
            .map(|f| Self::selected_label(&f).replace("Detect automatically", "Auto"))
            .unwrap_or_default();
        let preferences = &self.state["preferences"];
        let targets = preferences["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str())
            .collect::<Vec<_>>();
        let translate_label = if preferences["displayMode"] == "Original" || targets.is_empty() {
            "No translation".to_owned()
        } else {
            format!("→ {}", targets.join(", "))
        };
        let transcript = preferences["captionPresentation"] == "Transcript";
        let red = rgb(0xe5484d);
        div()
            .id("control-bar")
            .occlude()
            .flex()
            .items_center()
            .gap(px(4.))
            .p(px(6.))
            .rounded(px(16.))
            .bg(p.menu)
            .border_1()
            .border_color(p.menu_line)
            .shadow_lg()
            .text_color(p.text)
            .text_size(px(13.))
            .on_hover(cx.listener(|this, hovered: &bool, _, cx| {
                this.bar_hover = *hovered;
                if !*hovered {
                    this.controls_until = Some(Instant::now() + REVEAL);
                }
                cx.notify();
            }))
            .child(
                div()
                    .id("bar-listen")
                    .h(px(36.))
                    .px(px(14.))
                    .flex()
                    .items_center()
                    .gap(px(8.))
                    .rounded(px(11.))
                    .font_weight(FontWeight::SEMIBOLD)
                    .text_color(white())
                    .bg(if active { red } else { rgb(0x2f6fe8) })
                    .when(locked, |el| el.opacity(0.5))
                    .when(!locked, |el| el.cursor_pointer())
                    .child(
                        div()
                            .size(px(10.))
                            .rounded(px(if active { 2. } else { 5. }))
                            .bg(white()),
                    )
                    .child(listen_label)
                    .on_click(cx.listener(|this, _, _, cx| this.listen(cx))),
            )
            .child(Self::bar_separator(p))
            .child(
                self.bar_menu_anchor(
                    "listening",
                    [audio, spoken]
                        .into_iter()
                        .filter(|s| !s.is_empty())
                        .collect::<Vec<_>>()
                        .join(" · "),
                    false,
                    p,
                    cx,
                ),
            )
            .child(self.bar_menu_anchor("translate", translate_label, false, p, cx))
            .child(Self::bar_separator(p))
            .child(
                div()
                    .flex()
                    .p(px(3.))
                    .rounded(px(10.))
                    .bg(p.control)
                    .children([("Captions", false), ("Transcript", true)].into_iter().map(
                        |(label, value)| {
                            let on = transcript == value;
                            div()
                                .id(SharedString::from(format!("layout-{label}")))
                                .h(px(30.))
                                .px(px(12.))
                                .flex()
                                .items_center()
                                .rounded(px(8.))
                                .font_weight(FontWeight::MEDIUM)
                                .when(on, |el| el.bg(p.control_on).shadow_sm())
                                .when(!on, |el| {
                                    el.text_color(p.muted)
                                        .cursor_pointer()
                                        .hover(move |s| s.text_color(p.text))
                                })
                                .child(label)
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    if !on {
                                        this.set_layout(value, cx)
                                    }
                                }))
                        },
                    )),
            )
            .child(Self::bar_separator(p))
            .child(self.bar_menu_anchor("style", "Aa", true, p, cx))
            .child(self.bar_menu_anchor("more", "•••", true, p, cx))
    }

    /// Captions and Transcript are the two presentations of the same canvas.
    pub(super) fn set_layout(&mut self, transcript: bool, cx: &mut Context<Self>) {
        let preferences = &self.state["preferences"];
        if transcript {
            for key in ["captionPresentation", "translationPresentation"] {
                if preferences[key] != "Transcript" {
                    self.caption_presentation
                        .insert(key, preferences[key].clone());
                }
            }
        }
        for key in ["captionPresentation", "translationPresentation"] {
            let value = if transcript {
                json!("Transcript")
            } else {
                self.caption_presentation
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| json!("Rolling lines"))
            };
            self.command(json!({"type":"set","key":key,"value":value}), cx);
        }
    }

    pub(super) fn settings_sheet(&self, p: Palette, cx: &mut Context<Self>) -> Div {
        div()
            .absolute()
            .inset_0()
            .flex()
            .items_center()
            .justify_center()
            .bg(gpui::black().opacity(0.45))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    this.settings_open = false;
                    this.picker = None;
                    cx.notify();
                }),
            )
            .child(
                div()
                    .id("settings-sheet")
                    .occlude()
                    .w(px(560.))
                    .max_h(relative(0.86))
                    .flex()
                    .flex_col()
                    .rounded(px(12.))
                    .bg(p.panel)
                    .border_1()
                    .border_color(p.menu_line)
                    .shadow_lg()
                    .text_color(p.text)
                    .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                    .on_drop::<ExternalPaths>(cx.listener(|this, paths: &ExternalPaths, _, cx| {
                        let paths = paths
                            .paths()
                            .iter()
                            .map(|p| p.to_string_lossy().to_string())
                            .collect::<Vec<_>>();
                        this.command(json!({"type":"dropRoots","paths":paths}), cx)
                    }))
                    .child(
                        div()
                            .h(px(48.))
                            .flex_none()
                            .px_4()
                            .flex()
                            .items_center()
                            .justify_between()
                            .border_b_1()
                            .border_color(p.line)
                            .child(
                                div()
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child("Models & Settings"),
                            )
                            .child(
                                div()
                                    .id("settings-done")
                                    .px(px(12.))
                                    .py(px(4.))
                                    .rounded(px(6.))
                                    .bg(p.accent)
                                    .text_color(white())
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .cursor_pointer()
                                    .child("Done")
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.settings_open = false;
                                        this.picker = None;
                                        cx.notify();
                                    })),
                            ),
                    )
                    .child(
                        div()
                            .id("settings-scroll")
                            .flex_1()
                            .min_h_0()
                            .overflow_y_scroll()
                            .p_4()
                            .flex()
                            .flex_col()
                            .gap(px(18.))
                            .when(self.tab_locked(MODELS), |el| {
                                el.child(self.lock_banner(p, cx))
                            })
                            .children(
                                self.sections(MODELS)
                                    .iter()
                                    .map(|section| self.section_group(section, p, cx)),
                            ),
                    ),
            )
    }

    /// Errors and notices float at the top; they are not hidden with the controls.
    pub(super) fn toast(&self, p: Palette, cx: &mut Context<Self>) -> Option<Div> {
        let error = self.state["error"].as_str().unwrap_or("").to_owned();
        let notice = [
            self.state["notice"].as_str().unwrap_or(""),
            self.update_status.message.as_str(),
        ]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" · ");
        if error.is_empty() && notice.is_empty() {
            return None;
        }
        let is_error = !error.is_empty();
        let small = |id: &'static str, label: &'static str| {
            div()
                .id(id)
                .flex_none()
                .px(px(10.))
                .py(px(3.))
                .rounded(px(6.))
                .bg(p.control)
                .text_size(px(12.))
                .font_weight(FontWeight::MEDIUM)
                .cursor_pointer()
                .hover(move |s| s.bg(p.control_on))
                .child(label)
        };
        Some(
            div()
                .absolute()
                .top(px(14.))
                .left_0()
                .right_0()
                .flex()
                .justify_center()
                .child(
                    div()
                        .id("toast")
                        .occlude()
                        .max_w(px(640.))
                        .flex()
                        .items_center()
                        .gap_3()
                        .pl(px(14.))
                        .pr(px(8.))
                        .py(px(8.))
                        .rounded(px(10.))
                        .bg(p.menu)
                        .border_1()
                        .border_color(p.menu_line)
                        .shadow_lg()
                        .text_color(p.text)
                        .text_size(px(13.))
                        .when(is_error, |el| {
                            el.child(
                                div()
                                    .size(px(8.))
                                    .flex_none()
                                    .rounded_full()
                                    .bg(rgb(0xe5484d)),
                            )
                        })
                        .child(div().min_w_0().child(if is_error { error } else { notice }))
                        .when(self.flag("capturePermissionIssue") && is_error, |el| {
                            el.child(small("toast-settings", "Open System Settings").on_click(
                                cx.listener(|this, _, _, cx| {
                                    this.command(json!({"type":"captureSettings"}), cx)
                                }),
                            ))
                        })
                        .child(small("toast-dismiss", "Dismiss").on_click(cx.listener(
                            move |this, _, _, cx| {
                                this.command(
                                json!({"type":if is_error {"dismissError"} else {"dismissNotice"}}),
                                cx,
                            )
                            },
                        ))),
                ),
        )
    }

    pub(super) fn handle_escape(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.picker.take().is_some() || self.menu.take().is_some() {
            cx.notify();
        } else if self.settings_open {
            self.settings_open = false;
            cx.notify();
        } else if window.is_fullscreen() {
            window.toggle_fullscreen();
        }
    }
}
