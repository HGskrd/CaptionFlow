use super::{CaptionFlow, Palette, color};
use gpui::{
    Animation, AnimationExt, Context, Div, FontWeight, ScrollWheelEvent, SharedString, TextRun,
    UniformListScrollHandle, Window, div, font, point, prelude::*, px, relative, uniform_list,
};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

#[derive(Default)]
pub(super) struct PresentationState {
    session: String,
    modes: Vec<String>,
    viewport: Option<(u32, u32)>,
    scrolls: HashMap<String, UniformListScrollHandle>,
    paused: HashSet<String>,
    pending_scroll: HashSet<String>,
    motions: HashMap<String, LineMotion>,
}
struct LineMotion {
    from: f32,
    to: f32,
    began: Instant,
    generation: usize,
}
impl LineMotion {
    fn position(&self) -> f32 {
        let progress = (self.began.elapsed().as_secs_f32() / 0.16).min(1.);
        self.from + (self.to - self.from) * (1. - (1. - progress).powi(3))
    }
}

fn speaker_color(speaker: &str, light_background: bool) -> gpui::Hsla {
    let hash = speaker.bytes().fold(2166136261u32, |hash, byte| {
        (hash ^ byte as u32).wrapping_mul(16777619)
    });
    let hues = [0.56, 0.09, 0.77, 0.36, 0.94, 0.49, 0.16, 0.66];
    gpui::Hsla {
        h: hues[(hash as usize) % hues.len()],
        s: 0.72,
        l: if light_background { 0.34 } else { 0.73 },
        a: 1.,
    }
}
fn render_line(
    row: &Value,
    key: &str,
    line_height: f32,
    animate: bool,
    light_background: bool,
) -> gpui::Stateful<Div> {
    div()
        .id(SharedString::from(key.to_owned()))
        .flex()
        .h(px(line_height))
        .flex_none()
        .line_height(px(line_height))
        .when_some(
            row["speaker_id"].as_str().filter(|s| !s.is_empty()),
            |el, speaker| el.text_color(speaker_color(speaker, light_background)),
        )
        .children(row["words"].as_array().into_iter().flatten().map(|word| {
            let id = format!("{key}/{}", word["id"].as_str().unwrap_or(""));
            let element = div()
                .id(SharedString::from(id.clone()))
                .w(px(word["width"].as_f64().unwrap_or(0.) as f32))
                .h(px(line_height))
                .flex_none()
                .whitespace_nowrap()
                .child(word["text"].as_str().unwrap_or("").to_owned());
            if animate && word["entering"].as_bool().unwrap_or(false) {
                element
                    .with_animation(
                        SharedString::from(format!("appear/{id}")),
                        Animation::new(Duration::from_millis(100)),
                        |el, t| el.opacity(t),
                    )
                    .into_any_element()
            } else {
                element.into_any_element()
            }
        }))
}

impl CaptionFlow {
    pub(super) fn back_to_live(&mut self, lane: Option<&str>, cx: &mut Context<Self>) {
        if self.state["canvas"]["linked"].as_bool().unwrap_or(false) || lane.is_none() {
            self.presentation.paused.clear();
        } else if let Some(lane) = lane {
            self.presentation.paused.remove(lane);
        }
        self.command(json!({"type":"backToLive","lane":lane}), cx);
    }
    pub(super) fn render_canvas(
        &mut self,
        window: &Window,
        chrome: bool,
        top_inset: f32,
        cx: &mut Context<Self>,
    ) -> Div {
        let preferences = self.state["preferences"].clone();
        let data = self.state["canvas"].clone();
        let lanes = data["lanes"].as_array().cloned().unwrap_or_default();
        let session = data["session"].as_str().unwrap_or("").to_owned();
        let modes = lanes
            .iter()
            .map(|v| format!("{}:{}", v["id"], v["mode"]))
            .collect::<Vec<_>>();
        if session != self.presentation.session || modes != self.presentation.modes {
            self.presentation.session = session.clone();
            self.presentation.modes = modes;
            self.presentation.motions.clear();
            self.presentation.paused.clear();
            self.presentation.viewport = None;
        }
        let foreground = color(&preferences["foreground"]);
        let background = color(&preferences["background"]);
        let light_background = preferences["background"]["r"].as_f64().unwrap_or(0.) * 0.2126
            + preferences["background"]["g"].as_f64().unwrap_or(0.) * 0.7152
            + preferences["background"]["b"].as_f64().unwrap_or(0.) * 0.0722
            > 0.5;
        let font_size = preferences["fontSize"].as_f64().unwrap_or(54.) as f32;
        let family = data["fontFamily"]
            .as_str()
            .unwrap_or(".SystemUIFont")
            .to_owned();
        let weight = if data["systemFont"].as_bool().unwrap_or(true) {
            FontWeight::MEDIUM
        } else {
            FontWeight::NORMAL
        };
        let animate = data["gentleAnimation"].as_bool().unwrap_or(false);
        let line_height = data["lineHeight"]
            .as_f64()
            .unwrap_or(font_size as f64 * 1.3) as f32;
        let linked = data["linked"].as_bool().unwrap_or(false);
        let viewport = window.viewport_size();
        let width = f32::from(viewport.width).max(1.);
        let height = (f32::from(viewport.height) - top_inset).max(1.);
        let padding = (width * 0.065).max(32.);
        let usable = (width - padding * 2.).max(1.);
        let lane_height = ((height - 64. - lanes.len().saturating_sub(1) as f32 * 32.)
            / lanes.len().max(1) as f32)
            .max(font_size * 1.5);
        let geometry = (usable.to_bits(), lane_height.to_bits());
        if self.presentation.viewport != Some(geometry) {
            self.presentation.viewport = Some(geometry);
            self.engine
                .command(json!({"type":"viewport","width":usable,"height":lane_height}));
        }
        let empty = lanes.iter().all(|v| {
            v["rows"].as_array().is_none_or(|r| r.is_empty())
                && v["classicText"].as_str().is_none_or(|t| t.is_empty())
        });
        let p = Palette::new(matches!(
            window.appearance(),
            gpui::WindowAppearance::Dark | gpui::WindowAppearance::VibrantDark
        ));
        let mut rendered = Vec::new();
        let mut visible_motion_keys = HashSet::new();
        for lane in &lanes {
            let id = lane["id"].as_str().unwrap_or("original").to_owned();
            let mode = lane["mode"].as_str().unwrap_or("Classic");
            let rows = lane["rows"].as_array().cloned().unwrap_or_default();
            let lane_key = format!("{session}/{id}");
            let pane = div()
                .id(SharedString::from(format!("lane/{lane_key}")))
                .h(px(lane_height))
                .w_full()
                .flex_none()
                .font_family(SharedString::from(family.clone()))
                .font_weight(weight)
                .text_size(px(font_size))
                .text_color(foreground);
            if mode == "Transcript" {
                let handle = self
                    .presentation
                    .scrolls
                    .entry(id.clone())
                    .or_default()
                    .clone();
                let base = handle.0.borrow().base_handle.clone();
                let follows = lane["followsLive"].as_bool().unwrap_or(true);
                if follows && !self.presentation.paused.contains(&id) {
                    base.scroll_to_bottom();
                } else if !follows {
                    base.set_offset(point(
                        px(0.),
                        px(-(lane["offset"].as_f64().unwrap_or(0.) as f32)),
                    ));
                }
                let footer = if chrome { 28. } else { 0. };
                let list_id = SharedString::from(format!("transcript/{lane_key}"));
                let row_key = lane_key.clone();
                let list = uniform_list(list_id, rows.len(), move |range, _, _| {
                    range
                        .map(|index| {
                            let row = &rows[index];
                            let key = format!("{row_key}/{}", row["id"].as_str().unwrap_or(""));
                            render_line(row, &key, line_height, animate, light_background)
                        })
                        .collect::<Vec<_>>()
                })
                .h(px((lane_height - footer).max(1.)))
                .w_full()
                .track_scroll(handle)
                .on_scroll_wheel(cx.listener({
                    let id = id.clone();
                    let base = base.clone();
                    move |this, _: &ScrollWheelEvent, window, cx| {
                        if linked {
                            for value in this.state["canvas"]["lanes"]
                                .as_array()
                                .into_iter()
                                .flatten()
                            {
                                if let Some(key) = value["id"].as_str() {
                                    this.presentation.paused.insert(key.to_owned());
                                }
                            }
                        } else {
                            this.presentation.paused.insert(id.clone());
                        }
                        if this.presentation.pending_scroll.insert(id.clone()) {
                            let lane = id.clone();
                            let handle = base.clone();
                            cx.on_next_frame(window, move |this, _, cx| {
                                this.presentation.pending_scroll.remove(&lane);
                                let offset = -f32::from(handle.offset().y);
                                this.command(
                                    json!({"type":"scroll","lane":lane,"offset":offset}),
                                    cx,
                                );
                            });
                        }
                        cx.notify();
                    }
                }));
                let paused = !follows || self.presentation.paused.contains(&id);
                let pending = lane["pending"].as_bool().unwrap_or(false);
                rendered.push(
                    pane.flex()
                        .flex_col()
                        .child(list)
                        .when(chrome, |el| {
                            el.child(
                                div()
                                    .h(px(28.))
                                    .flex_none()
                                    .flex()
                                    .items_center()
                                    .justify_between()
                                    .text_size(px(11.))
                                    .child(if pending {
                                        "This passage is not available yet."
                                    } else if linked {
                                        "Linked scrolling"
                                    } else {
                                        ""
                                    })
                                    .child(
                                        Self::button(
                                            SharedString::from(format!("live/{id}")),
                                            if paused { "Back to live" } else { "Live" },
                                            false,
                                            !paused,
                                            p,
                                        )
                                        .on_click(
                                            cx.listener({
                                                let id = id.clone();
                                                move |this, _, _, cx| {
                                                    this.back_to_live(Some(&id), cx)
                                                }
                                            }),
                                        ),
                                    ),
                            )
                        })
                        .into_any_element(),
                );
            } else if mode == "Classic" {
                let text = lane["classicText"].as_str().unwrap_or("");
                let mut selected_font = font(SharedString::from(family.clone()));
                selected_font.weight = weight;
                let measure = |value: &str| {
                    let flat = value.replace(['\n', '\r'], " ");
                    let run = TextRun {
                        len: flat.len(),
                        font: selected_font.clone(),
                        color: foreground,
                        background_color: None,
                        underline: None,
                        strikethrough: None,
                    };
                    f32::from(
                        window
                            .text_system()
                            .shape_line(flat.into(), px(font_size), &[run], None)
                            .width,
                    )
                };
                let capacity = (lane["capacity"].as_u64().unwrap_or(3) as usize).max(1);
                let lines = super::captions::page(text, usable, capacity, measure);
                rendered.push(
                    pane.flex()
                        .flex_col()
                        .justify_center()
                        .overflow_hidden()
                        .child(
                            div()
                                .h(px(capacity as f32 * line_height))
                                .flex_none()
                                .children(lines.into_iter().map(|line| {
                                    div()
                                        .h(px(line_height))
                                        .line_height(px(line_height))
                                        .whitespace_nowrap()
                                        .child(line)
                                })),
                        )
                        .into_any_element(),
                );
            } else {
                let capacity = lane["capacity"].as_u64().unwrap_or(3) as usize;
                let mut elements = Vec::new();
                for (index, row) in rows.iter().enumerate() {
                    let key = format!("{lane_key}/{}", row["id"].as_str().unwrap_or(""));
                    visible_motion_keys.insert(key.clone());
                    let top = index as f32 * line_height;
                    let motion =
                        self.presentation
                            .motions
                            .entry(key.clone())
                            .or_insert(LineMotion {
                                from: top,
                                to: top,
                                began: Instant::now(),
                                generation: 0,
                            });
                    if !animate {
                        motion.from = top;
                        motion.to = top;
                    } else if motion.to != top {
                        motion.from = motion.position();
                        motion.to = top;
                        motion.began = Instant::now();
                        motion.generation += 1;
                    }
                    let row = render_line(row, &key, line_height, animate, light_background)
                        .absolute()
                        .left_0()
                        .top(px(top));
                    if animate
                        && motion.from != motion.to
                        && motion.began.elapsed() < Duration::from_millis(160)
                    {
                        let from = motion.from;
                        let to = motion.to;
                        let generation = motion.generation;
                        elements.push(
                            row.with_animation(
                                SharedString::from(format!("move/{key}/{generation}")),
                                Animation::new(Duration::from_millis(160)),
                                move |el, t| {
                                    el.top(px(from + (to - from) * (1. - (1. - t).powi(3))))
                                },
                            )
                            .into_any_element(),
                        );
                    } else {
                        elements.push(row.into_any_element());
                    }
                }
                rendered.push(
                    pane.flex()
                        .flex_col()
                        .justify_center()
                        .overflow_hidden()
                        .child(
                            div()
                                .relative()
                                .h(px(capacity as f32 * line_height))
                                .w_full()
                                .flex_none()
                                .overflow_hidden()
                                .children(elements),
                        )
                        .into_any_element(),
                );
            }
            if chrome && let Some(content) = rendered.pop() {
                let label = if id == "original" {
                    "Original"
                } else {
                    id.strip_prefix("translation:").unwrap_or(&id)
                };
                rendered.push(
                    div()
                        .relative()
                        .child(content)
                        .child(
                            // Sits in the left margin so it never covers caption text.
                            div()
                                .absolute()
                                .top(px(6.))
                                .right(relative(1.))
                                .pr(px(14.))
                                .whitespace_nowrap()
                                .text_size(px(11.))
                                .text_color(p.muted)
                                .child(label.to_owned()),
                        )
                        .into_any_element(),
                );
            }
        }
        self.presentation
            .motions
            .retain(|key, _| visible_motion_keys.contains(key));
        div()
            .flex_1()
            .min_w_0()
            .h_full()
            .bg(background)
            .text_color(foreground)
            .overflow_hidden()
            .when(empty, |el| {
                el.flex().items_center().justify_center().child(
                    div().text_size(px(26.)).opacity(0.7).child(
                        if self.flag("busy") || self.flag("installing") {
                            self.state["phase"].as_str().unwrap_or("").to_owned()
                        } else if self.flag("listening") {
                            self.state["audioState"]
                                .as_str()
                                .unwrap_or("Listening…")
                                .to_owned()
                        } else {
                            "Ready to listen".to_owned()
                        },
                    ),
                )
            })
            .when(!empty, |el| {
                el.child(
                    div()
                        .id("canvas-scroll")
                        .size_full()
                        .overflow_y_scroll()
                        .child(
                            div()
                                .min_h_full()
                                .flex()
                                .flex_col()
                                .justify_center()
                                .gap_8()
                                .px(px(padding))
                                .py_8()
                                .children(rendered),
                        ),
                )
            })
    }
}

#[cfg(test)]
mod speaker_tests {
    use super::*;
    #[test]
    fn colors_are_stable_across_lanes_and_adapt_to_background() {
        assert_eq!(speaker_color("1", false), speaker_color("1", false));
        assert_ne!(speaker_color("1", false), speaker_color("2", false));
        assert!(speaker_color("1", false).l > speaker_color("1", true).l);
    }
}
