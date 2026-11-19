//! Settings controls: grouped rows, real popover menus, switches, segmented
//! choices and steppers, all driven by the engine's `sections` snapshot.
use super::{CaptionFlow, Palette, color, string};
use gpui::{
    AnyElement, Context, Corner, Div, FontWeight, SharedString, Stateful, anchored, deferred, div,
    point, prelude::*, px, relative, rgb, white,
};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

/// Preset swatches mirror the engine's `preset` command colors.
const PRESETS: [(&str, u32, u32); 3] = [
    ("Midnight", 0x111316, 0xf5f5f0),
    ("Paper", 0xf5f2e8, 0x24292b),
    ("Cinema", 0x000000, 0xffdb4d),
];

pub(super) fn switch(on: bool, disabled: bool, p: Palette) -> Div {
    div()
        .w(px(30.))
        .h(px(18.))
        .flex_none()
        .relative()
        .rounded_full()
        .bg(if on { rgb(0x30a46c).into() } else { p.control })
        .when(disabled, |el| el.opacity(0.45))
        .child(
            div()
                .absolute()
                .top(px(2.))
                .left(px(if on { 14. } else { 2. }))
                .size(px(14.))
                .rounded_full()
                .bg(white())
                .shadow_sm(),
        )
}

/// A menu row: check column, label, optional trailing text (value or shortcut).
pub(super) fn menu_item(
    id: impl Into<gpui::ElementId>,
    checked: bool,
    label: impl Into<SharedString>,
    trailing: Option<String>,
    disabled: bool,
    p: Palette,
) -> Stateful<Div> {
    div()
        .id(id)
        .relative()
        .flex()
        .items_center()
        .gap(px(6.))
        .h(px(26.))
        .pl(px(6.))
        .pr(px(10.))
        .rounded(px(6.))
        .whitespace_nowrap()
        .child(
            div()
                .w(px(16.))
                .min_w(px(16.))
                .flex_none()
                .text_center()
                .text_size(px(12.))
                .child(if checked { "✓" } else { "" }),
        )
        .child(label.into())
        // Trailing text is positioned, not flowed, so every row's label starts
        // at the same x whether or not it has a shortcut.
        .when_some(trailing.filter(|t| !t.is_empty()), |el, text| {
            el.pr(px(64.)).child(
                div()
                    .absolute()
                    .top_0()
                    .bottom_0()
                    .right(px(10.))
                    .flex()
                    .items_center()
                    .text_size(px(12.))
                    .text_color(p.muted)
                    .child(text),
            )
        })
        .when(disabled, |el| el.opacity(0.4))
        .when(!disabled, |el| {
            el.cursor_pointer()
                .hover(move |s| s.bg(p.accent).text_color(white()))
        })
}

/// Float `content` above everything else, attached at the current layout position.
pub(super) fn overlay(corner: Corner, dy: f32, content: impl IntoElement) -> AnyElement {
    deferred(
        anchored()
            .anchor(corner)
            .offset(point(px(0.), px(dy)))
            .snap_to_window_with_margin(px(8.))
            .child(content),
    )
    .with_priority(1)
    .into_any_element()
}

fn option_label(options: &[Value], value: &Value) -> String {
    options
        .iter()
        .find(|v| v["value"] == *value)
        .map(|v| string(&v["label"]))
        .unwrap_or_else(|| string(value))
}

impl CaptionFlow {
    /// Open or close the menu `key`. A click on the trigger that just closed the
    /// menu via mouse-down-outside must not reopen it.
    pub(super) fn toggle_picker(&mut self, key: &str, disabled: bool, cx: &mut Context<Self>) {
        if disabled {
            return;
        }
        if let Some((closed, at)) = &self.picker_closed
            && closed == key
            && at.elapsed() < Duration::from_millis(250)
        {
            self.picker_closed = None;
            return;
        }
        self.picker = if self.picker.as_deref() == Some(key) {
            None
        } else {
            Some(key.to_owned())
        };
        cx.notify();
    }

    /// The floating menu surface. Clicking anywhere outside closes it.
    pub(super) fn menu_panel(
        &self,
        key: &str,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Stateful<Div> {
        let key = key.to_owned();
        div()
            .id(SharedString::from(format!("menu-{key}")))
            .occlude()
            .min_w(px(200.))
            .max_h(px(360.))
            .overflow_y_scroll()
            .p(px(5.))
            .rounded(px(10.))
            .bg(p.menu)
            .border_1()
            .border_color(p.menu_line)
            .shadow_lg()
            .text_size(px(13.))
            .text_color(p.text)
            .on_mouse_down_out(cx.listener(move |this, _, _, cx| {
                if this.picker.as_deref() == Some(key.as_str()) {
                    this.picker = None;
                    this.picker_closed = Some((key.clone(), Instant::now()));
                    cx.notify();
                }
            }))
    }

    fn option_menu(
        &self,
        key: &str,
        options: &[Value],
        value: &Value,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Stateful<Div> {
        self.menu_panel(key, p, cx)
            .children(options.iter().enumerate().map(|(i, option)| {
                let chosen = option["value"].clone();
                let key = key.to_owned();
                menu_item(
                    SharedString::from(format!("option-{key}-{i}")),
                    chosen == *value,
                    string(&option["label"]),
                    None,
                    false,
                    p,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.command(json!({"type":"set","key":key,"value":chosen}), cx)
                }))
            }))
    }

    /// A Mac-style pop-up button that opens a checkmarked menu below itself.
    pub(super) fn popup(
        &self,
        key: &str,
        options: &[Value],
        value: &Value,
        disabled: bool,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Div {
        let open = self.picker.as_deref() == Some(key) && !disabled && self.menu.is_none();
        let trigger_key = key.to_owned();
        div()
            .relative()
            .flex_none()
            .child(
                div()
                    .id(SharedString::from(format!("popup-{key}")))
                    .flex()
                    .items_center()
                    .gap(px(6.))
                    .h(px(24.))
                    .px(px(8.))
                    .max_w(px(196.))
                    .rounded(px(6.))
                    .bg(if open { p.control_on } else { p.control })
                    .text_size(px(12.))
                    .font_weight(FontWeight::MEDIUM)
                    .when(disabled, |el| el.opacity(0.45))
                    .when(!disabled, |el| {
                        el.cursor_pointer().hover(move |s| s.bg(p.control_on))
                    })
                    .child(
                        div()
                            .min_w_0()
                            .truncate()
                            .child(option_label(options, value)),
                    )
                    .child(div().text_size(px(9.)).text_color(p.muted).child("▼"))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.toggle_picker(&trigger_key, disabled, cx)
                    })),
            )
            .when(open, |el| {
                el.child(div().absolute().top(relative(1.)).right_0().child(overlay(
                    Corner::TopRight,
                    4.,
                    self.option_menu(key, options, value, p, cx),
                )))
            })
    }

    /// Inline choice for short option lists (Both / Original / Translation, 2 / 3 / 4).
    pub(super) fn segmented(
        &self,
        key: &str,
        options: &[Value],
        value: &Value,
        disabled: bool,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Div {
        div()
            .flex()
            .flex_none()
            .p(px(2.))
            .rounded(px(7.))
            .bg(p.control)
            .when(disabled, |el| el.opacity(0.45))
            .children(options.iter().enumerate().map(|(i, option)| {
                let on = option["value"] == *value;
                let chosen = option["value"].clone();
                let key = key.to_owned();
                div()
                    .id(SharedString::from(format!("seg-{key}-{i}")))
                    .px(px(9.))
                    .py(px(3.))
                    .rounded(px(5.))
                    .text_size(px(12.))
                    .font_weight(FontWeight::MEDIUM)
                    .when(on, |el| el.bg(p.control_on).text_color(p.text).shadow_sm())
                    .when(!on, |el| el.text_color(p.muted))
                    .when(!disabled && !on, |el| {
                        el.cursor_pointer().hover(move |s| s.text_color(p.text))
                    })
                    .child(string(&option["label"]))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled && !on {
                            this.command(json!({"type":"set","key":key,"value":chosen}), cx)
                        }
                    }))
            }))
    }

    pub(super) fn select(
        &self,
        key: &str,
        options: &[Value],
        value: &Value,
        disabled: bool,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> Div {
        let short = options.len() <= 3
            && options
                .iter()
                .all(|o| string(&o["label"]).chars().count() <= 12);
        if short {
            self.segmented(key, options, value, disabled, p, cx)
        } else {
            self.popup(key, options, value, disabled, p, cx)
        }
    }

    fn stepper(&self, field: &Value, p: Palette, cx: &mut Context<Self>) -> Div {
        let key = field["key"].as_str().unwrap_or("").to_owned();
        let disabled = field["disabled"].as_bool().unwrap_or(false);
        let current = field["value"].as_f64().unwrap_or(0.);
        let step = field["step"].as_f64().unwrap_or(1.);
        let minimum = field["min"].as_f64().unwrap_or(0.);
        let maximum = field["max"].as_f64().unwrap_or(120.);
        let text = if key == "silenceThreshold" {
            format!("{current:.3}")
        } else if step.fract() != 0. {
            format!("{current:.1}")
        } else {
            format!("{current:.0}")
        };
        let arrow = |i: usize, direction: f64| {
            let key = key.clone();
            div()
                .id(SharedString::from(format!("step-{key}-{i}")))
                .size(px(24.))
                .flex()
                .items_center()
                .justify_center()
                .rounded(px(6.))
                .bg(p.control)
                .text_size(px(14.))
                .when(!disabled, |el| {
                    el.cursor_pointer().hover(move |s| s.bg(p.control_on))
                })
                .child(if i == 0 { "−" } else { "+" })
                .on_click(cx.listener(move |this, _, _, cx| {
                    if disabled {
                        return;
                    }
                    let number = ((current + direction * step).clamp(minimum, maximum) * 1000.)
                        .round()
                        / 1000.;
                    let value = if step.fract() == 0. {
                        json!(number as i64)
                    } else {
                        json!(number)
                    };
                    this.command(json!({"type":"set","key":key,"value":value}), cx);
                }))
        };
        div()
            .flex()
            .flex_none()
            .items_center()
            .gap(px(2.))
            .when(disabled, |el| el.opacity(0.45))
            .child(arrow(0, -1.))
            .child(
                div()
                    .min_w(px(48.))
                    .text_center()
                    .text_size(px(12.))
                    .child(text),
            )
            .child(arrow(1, 1.))
    }

    fn small_button(
        &self,
        id: impl Into<gpui::ElementId>,
        label: impl Into<SharedString>,
        disabled: bool,
        p: Palette,
    ) -> Stateful<Div> {
        div()
            .id(id)
            .flex_none()
            .h(px(24.))
            .px(px(10.))
            .flex()
            .items_center()
            .rounded(px(6.))
            .bg(p.control)
            .text_size(px(12.))
            .font_weight(FontWeight::MEDIUM)
            .when(disabled, |el| el.opacity(0.45))
            .when(!disabled, |el| {
                el.cursor_pointer().hover(move |s| s.bg(p.control_on))
            })
            .child(label.into())
    }

    /// One settings row: label on the left, its control on the right.
    pub(super) fn field_row(
        &self,
        field: &Value,
        p: Palette,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let kind = field["kind"].as_str().unwrap_or("");
        let key = field["key"].as_str().unwrap_or("").to_owned();
        let label = field["label"].as_str().unwrap_or("").to_owned();
        let value = field["value"].clone();
        let disabled = field["disabled"].as_bool().unwrap_or(false);
        let row = div()
            .id(SharedString::from(format!("field-{kind}-{key}")))
            .flex()
            .items_center()
            .justify_between()
            .gap_3()
            .min_h(px(36.))
            .px_3()
            .py(px(6.));
        let label_el = |text: String| div().min_w_0().truncate().child(text);
        match kind {
            "select" => {
                let options = field["options"].as_array().cloned().unwrap_or_default();
                row.child(label_el(label))
                    .child(self.select(&key, &options, &value, disabled, p, cx))
                    .into_any_element()
            }
            "toggle" => {
                let checked = value.as_bool().unwrap_or(false);
                row.child(label_el(label))
                    .child(switch(checked, disabled, p))
                    .when(!disabled, |el| el.cursor_pointer())
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.command(json!({"type":"set","key":key,"value":!checked}), cx)
                        }
                    }))
                    .into_any_element()
            }
            "target" => {
                let checked = value.as_bool().unwrap_or(false);
                row.justify_start()
                    .gap_2()
                    .min_h(px(30.))
                    .py(px(4.))
                    .child(
                        div()
                            .size(px(16.))
                            .flex_none()
                            .flex()
                            .items_center()
                            .justify_center()
                            .rounded(px(4.))
                            .text_size(px(11.))
                            .when(checked, |el| el.bg(p.accent).text_color(white()).child("✓"))
                            .when(!checked, |el| el.border_1().border_color(p.muted)),
                    )
                    .child(label)
                    .when(disabled, |el| el.opacity(0.45))
                    .when(!disabled, |el| {
                        el.cursor_pointer().hover(move |s| s.bg(p.control))
                    })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if disabled {
                            return;
                        }
                        let mut targets = this.state["preferences"]["targets"]
                            .as_array()
                            .cloned()
                            .unwrap_or_default();
                        if checked {
                            targets.retain(|v| v.as_str() != Some(key.as_str()));
                        } else {
                            targets.push(json!(key));
                        }
                        this.command(json!({"type":"set","key":"targets","value":targets}), cx);
                    }))
                    .into_any_element()
            }
            "number" => row
                .child(label_el(label))
                .child(self.stepper(field, p, cx))
                .into_any_element(),
            "color" => row
                .child(label_el(label))
                .child(
                    div()
                        .size(px(22.))
                        .flex_none()
                        .rounded(px(6.))
                        .border_1()
                        .border_color(p.line)
                        .bg(color(&self.state["preferences"][&key])),
                )
                .cursor_pointer()
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.command(json!({"type":"color","key":key}), cx)
                }))
                .into_any_element(),
            "presets" => row
                .gap_2()
                .children(PRESETS.into_iter().enumerate().map(|(i, (name, bg, fg))| {
                    div()
                        .flex_1()
                        .flex()
                        .flex_col()
                        .items_center()
                        .gap_1()
                        .child(
                            div()
                                .id(("preset", i))
                                .w_full()
                                .h(px(42.))
                                .flex()
                                .items_center()
                                .justify_center()
                                .rounded(px(8.))
                                .border_1()
                                .border_color(p.line)
                                .bg(rgb(bg))
                                .text_color(rgb(fg))
                                .text_size(px(16.))
                                .font_weight(FontWeight::SEMIBOLD)
                                .cursor_pointer()
                                .hover(move |s| s.border_color(p.accent))
                                .child("Aa")
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.command(json!({"type":"preset","value":name}), cx)
                                })),
                        )
                        .child(div().text_size(px(10.)).text_color(p.muted).child(name))
                }))
                .into_any_element(),
            "root" => row
                .child(
                    div().min_w_0().flex().flex_col().child(label).child(
                        div()
                            .text_size(px(10.))
                            .text_color(p.muted)
                            .truncate()
                            .child(string(&value)),
                    ),
                )
                .child(
                    self.small_button(
                        SharedString::from(format!("remove-{key}")),
                        "Remove",
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.command(json!({"type":"removeRoot","path":key}), cx);
                        }
                    })),
                )
                .into_any_element(),
            "edit" => {
                let shown = value.as_str().filter(|v| !v.is_empty()).map(str::to_owned);
                row.child(
                    div()
                        .flex_none()
                        .whitespace_nowrap()
                        .text_color(if disabled { p.muted } else { p.accent })
                        .child(label),
                )
                .when_some(shown, |el, v| {
                    el.child(
                        div()
                            .flex_1()
                            .text_right()
                            .min_w_0()
                            .truncate()
                            .text_size(px(12.))
                            .text_color(p.muted)
                            .child(v),
                    )
                })
                .when(disabled, |el| el.opacity(0.6))
                .when(!disabled, |el| {
                    el.cursor_pointer().hover(move |s| s.bg(p.control))
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    if !disabled {
                        this.command(json!({"type":"edit","key":key}), cx);
                    }
                }))
                .into_any_element()
            }
            "action" => row
                .justify_start()
                .child(
                    self.small_button(
                        SharedString::from(format!("action-{key}")),
                        label,
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.command(json!({"type":key}), cx);
                        }
                    })),
                )
                .into_any_element(),
            "status" => {
                let text = if string(&value).is_empty() || value.is_null() {
                    label
                } else {
                    format!("{label}: {}", string(&value))
                };
                let good = key == "installed" && !text.contains("required");
                row.justify_start()
                    .gap_2()
                    .when(key == "installed", |el| {
                        el.child(div().size(px(8.)).rounded_full().bg(if good {
                            rgb(0x30a46c)
                        } else {
                            rgb(0xe5a000)
                        }))
                    })
                    .child(text)
                    .into_any_element()
            }
            _ => row.child(label).into_any_element(),
        }
    }

    pub(super) fn section_group(&self, section: &Value, p: Palette, cx: &mut Context<Self>) -> Div {
        let fields = section["fields"].as_array().cloned().unwrap_or_default();
        let note = section["note"].as_str().unwrap_or("").trim().to_owned();
        div()
            .flex()
            .flex_col()
            .gap(px(6.))
            .child(
                div()
                    .px_1()
                    .text_size(px(12.))
                    .font_weight(FontWeight::SEMIBOLD)
                    .text_color(p.muted)
                    .child(string(&section["title"])),
            )
            .child(
                div()
                    .flex()
                    .flex_col()
                    .rounded(px(10.))
                    .bg(p.card)
                    .border_1()
                    .border_color(p.line)
                    .overflow_hidden()
                    .children(fields.iter().enumerate().map(|(i, field)| {
                        div()
                            .when(i > 0, |el| el.border_t_1().border_color(p.line))
                            .child(self.field_row(field, p, cx))
                    })),
            )
            .when(!note.is_empty(), |el| {
                el.child(
                    div()
                        .px_1()
                        .text_size(px(11.))
                        .text_color(p.muted)
                        .child(note),
                )
            })
    }

    /// Shown when the visible settings cannot change until listening stops.
    pub(super) fn lock_banner(&self, p: Palette, cx: &mut Context<Self>) -> Div {
        div()
            .flex()
            .items_center()
            .gap_2()
            .px_3()
            .py_2()
            .rounded(px(8.))
            .bg(p.accent.opacity(0.14))
            .text_size(px(12.))
            .child(
                div()
                    .flex_1()
                    .child("Stop listening to change these settings."),
            )
            .child(
                div()
                    .id("lock-stop")
                    .px(px(10.))
                    .py(px(3.))
                    .rounded(px(6.))
                    .bg(p.accent)
                    .text_color(white())
                    .font_weight(FontWeight::SEMIBOLD)
                    .cursor_pointer()
                    .child("Stop")
                    .on_click(cx.listener(|this, _, _, cx| this.listen(cx))),
            )
    }

    pub(super) fn tab_locked(&self, tab: usize) -> bool {
        self.flag("active")
            && self.state["sections"][tab]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|s| s["fields"].as_array().into_iter().flatten())
                .any(|f| f["disabled"].as_bool().unwrap_or(false))
    }
}
