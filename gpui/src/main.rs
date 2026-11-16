#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]
mod canvas;
mod captions;
mod engine;
mod updater;
use engine::Engine;
use gpui::{
    App, Application, Bounds, ClipboardItem, Context, Div, ExternalPaths, FocusHandle, FontWeight,
    Hsla, KeyBinding, Menu, MenuItem, MouseButton, SharedString, TitlebarOptions, Window,
    WindowAppearance, WindowBounds, WindowOptions, actions, div, point, prelude::*, px, rgb, size,
};
use serde_json::{Value, json};
use std::{rc::Rc, time::Duration};
actions!(
    captionflow,
    [
        Quit,
        Close,
        Listen,
        Preview,
        Inspector,
        Fullscreen,
        CopyCaptions,
        Privacy,
        Settings,
        Escape,
        BackToLive
    ]
);
#[derive(Clone, Copy)]
struct Palette {
    background: Hsla,
    panel: Hsla,
    text: Hsla,
    muted: Hsla,
    line: Hsla,
    button: Hsla,
}
impl Palette {
    fn new(dark: bool) -> Self {
        let values = if dark {
            [0x25272b, 0x202226, 0xf2f3f5, 0xa9afb9, 0x3b3e44, 0x34373e]
        } else {
            [0xf5f6f8, 0xebeef2, 0x202631, 0x646e7c, 0xd9dde4, 0xffffff]
        };
        let [background, panel, text, muted, line, button] = values.map(|v| rgb(v).into());
        Self {
            background,
            panel,
            text,
            muted,
            line,
            button,
        }
    }
}
fn string(v: &Value) -> String {
    v.as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| v.to_string())
}
fn color(v: &Value) -> Hsla {
    gpui::Rgba {
        r: v["r"].as_f64().unwrap_or(0.) as f32,
        g: v["g"].as_f64().unwrap_or(0.) as f32,
        b: v["b"].as_f64().unwrap_or(0.) as f32,
        a: v["a"].as_f64().unwrap_or(1.) as f32,
    }
    .into()
}
struct CaptionFlow {
    focus: FocusHandle,
    engine: Rc<Engine>,
    state: Value,
    inspector: bool,
    fullscreen_inspector: bool,
    was_fullscreen: bool,
    tab: usize,
    picker: Option<String>,
    quitting: bool,
    copied: bool,
    presentation: canvas::PresentationState,
    updates: updater::Updates,
    update_status: updater::Status,
    installing_update: bool,
}
impl CaptionFlow {
    fn new(window: &mut Window, cx: &mut Context<Self>, engine: Rc<Engine>) -> Self {
        let focus = cx.focus_handle();
        focus.focus(window);
        let state = engine.poll().unwrap_or(json!({}));
        Self {
            focus,
            engine,
            state,
            inspector: false,
            fullscreen_inspector: false,
            was_fullscreen: false,
            tab: 0,
            picker: None,
            quitting: false,
            copied: false,
            presentation: Default::default(),
            updates: updater::Updates::new(),
            update_status: Default::default(),
            installing_update: false,
        }
    }
    fn start_polling(cx: &mut Context<Self>) {
        cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(33))
                    .await;
                let keep = this
                    .update(cx, |this, cx| {
                        if let Some(state) = this.engine.poll() {
                            if state["original"] != this.state["original"]
                                || state["translations"] != this.state["translations"]
                            {
                                this.copied = false;
                            }
                            if this.state != state {
                                this.state = state;
                                cx.notify();
                            }
                        }
                        this.updates.set_online(this.flag("online"));
                        let status = this.updates.status();
                        if this.update_status != status {
                            this.update_status = status;
                            cx.notify();
                        }
                        if this.quitting && this.flag("shutdownComplete") {
                            if this.installing_update
                                && let Err(error) = this.updates.launch_helper()
                            {
                                this.updates.failed(error.to_string());
                                this.quitting = false;
                                this.installing_update = false;
                                return true;
                            }
                            cx.quit();
                            return false;
                        }
                        true
                    })
                    .unwrap_or(false);
                if !keep {
                    break;
                }
            }
        })
        .detach();
    }
    fn flag(&self, key: &str) -> bool {
        self.state[key].as_bool().unwrap_or(false)
    }
    fn command(&mut self, value: Value, cx: &mut Context<Self>) {
        self.engine.command(value);
        if let Some(state) = self.engine.poll() {
            self.state = state;
        }
        self.picker = None;
        cx.notify();
    }
    fn listen(&mut self, cx: &mut Context<Self>) {
        if self.flag("stopping") || self.flag("installing") {
            return;
        }
        self.command(
            json!({"type":if self.flag("active"){"stop"}else{"start"}}),
            cx,
        );
        if !self.flag("installed") {
            self.inspector = true;
            self.tab = 2;
        }
    }
    fn update_app(&mut self, cx: &mut Context<Self>) {
        match self.update_status.phase.as_str() {
            "available" => self.updates.download(),
            "ready" => {
                self.installing_update = true;
                self.request_quit(cx);
            }
            "checking" | "downloading" => {}
            _ => self.updates.check(),
        }
        cx.notify();
    }
    fn request_quit(&mut self, cx: &mut Context<Self>) {
        self.quitting = true;
        self.command(json!({"type":"shutdown"}), cx);
    }
    fn toggle_inspector(&mut self, window: &Window, cx: &mut Context<Self>) {
        if window.is_fullscreen() {
            self.fullscreen_inspector = !self.fullscreen_inspector;
        } else {
            self.inspector = !self.inspector;
        }
        self.picker = None;
        cx.notify();
    }
    fn copy(&mut self, cx: &mut Context<Self>) {
        let p = &self.state["preferences"];
        let mut lanes = Vec::new();
        if p["displayMode"] != "Translation" {
            lanes.push(self.state["original"].as_str().unwrap_or("").to_owned());
        }
        if p["displayMode"] != "Original" {
            for language in p["targets"].as_array().into_iter().flatten() {
                lanes.push(
                    self.state["translations"][language.as_str().unwrap_or("")]
                        .as_str()
                        .unwrap_or("")
                        .to_owned(),
                );
            }
        }
        cx.write_to_clipboard(ClipboardItem::new_string(lanes.join("\n\n")));
        self.copied = true;
        cx.notify();
    }
    fn button(
        id: impl Into<gpui::ElementId>,
        label: impl Into<SharedString>,
        primary: bool,
        disabled: bool,
        p: Palette,
    ) -> gpui::Stateful<Div> {
        div()
            .id(id)
            .px_3()
            .py_1p5()
            .rounded_md()
            .cursor_pointer()
            .text_size(px(12.))
            .bg(if primary {
                rgb(0x2468d8).into()
            } else {
                p.button
            })
            .text_color(if primary {
                rgb(0xffffff).into()
            } else {
                p.text
            })
            .when(disabled, |el| el.opacity(0.45).cursor_default())
            .when(!disabled, |el| {
                el.hover(move |s| {
                    s.bg(if primary {
                        rgb(0x1b59bd).into()
                    } else {
                        p.line
                    })
                })
            })
            .child(label.into())
    }
    fn field(&self, field: &Value, p: Palette, cx: &mut Context<Self>) -> gpui::AnyElement {
        let kind = field["kind"].as_str().unwrap_or("");
        let key = field["key"].as_str().unwrap_or("").to_owned();
        let label = field["label"].as_str().unwrap_or("").to_owned();
        let value = field["value"].clone();
        let disabled = field["disabled"].as_bool().unwrap_or(false);
        let mut row = div()
            .id(SharedString::from(format!("field-{kind}-{key}")))
            .flex()
            .flex_col()
            .gap_2()
            .py_2();
        match kind {
            "select" => {
                let options = field["options"].as_array().cloned().unwrap_or_default();
                let selected = options
                    .iter()
                    .find(|v| v["value"] == value)
                    .map(|v| string(&v["label"]))
                    .unwrap_or_else(|| string(&value));
                row = row
                    .child(div().text_color(p.muted).text_size(px(11.)).child(label))
                    .child(
                        Self::button(
                            SharedString::from(format!("select-{key}")),
                            format!("{selected}  ▾"),
                            false,
                            disabled,
                            p,
                        )
                        .on_click(cx.listener({
                            let key = key.clone();
                            move |this, _, _, cx| {
                                if !disabled {
                                    this.picker = if this.picker.as_ref() == Some(&key) {
                                        None
                                    } else {
                                        Some(key.clone())
                                    };
                                    cx.notify();
                                }
                            }
                        })),
                    );
                if self.picker.as_ref() == Some(&key) && !disabled {
                    row = row.child(
                        div()
                            .id(SharedString::from(format!("options-{key}")))
                            .max_h(px(240.))
                            .overflow_y_scroll()
                            .rounded_md()
                            .bg(p.button)
                            .children(options.into_iter().enumerate().map(|(i, option)| {
                                let chosen = option["value"].clone();
                                let key = key.clone();
                                div()
                                    .id(("option", i))
                                    .px_3()
                                    .py_2()
                                    .cursor_pointer()
                                    .hover(move |s| s.bg(p.line))
                                    .child(format!(
                                        "{}{}",
                                        if chosen == value { "✓  " } else { "" },
                                        string(&option["label"])
                                    ))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.command(
                                            json!({"type":"set","key":key,"value":chosen}),
                                            cx,
                                        )
                                    }))
                            })),
                    );
                }
            }
            "toggle" | "target" => {
                let checked = value.as_bool().unwrap_or(false);
                let target = kind == "target";
                row = row.child(
                    Self::button(
                        SharedString::from(format!("toggle-{key}")),
                        format!("{}  {label}", if checked { "☑" } else { "☐" }),
                        false,
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if disabled {
                            return;
                        }
                        let (key, value) = if target {
                            let mut targets = this.state["preferences"]["targets"]
                                .as_array()
                                .cloned()
                                .unwrap_or_default();
                            if checked {
                                targets.retain(|v| v.as_str() != Some(key.as_str()));
                            } else {
                                targets.push(json!(key));
                            }
                            ("targets", json!(targets))
                        } else {
                            (key.as_str(), json!(!checked))
                        };
                        this.command(json!({"type":"set","key":key,"value":value}), cx);
                    })),
                );
            }
            "number" => {
                let current = value.as_f64().unwrap_or(0.);
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
                row = row.child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .gap_2()
                        .child(label)
                        .child(div().flex().items_center().gap_2().children(
                            [-1., 1.].into_iter().enumerate().map(|(i, direction)| {
                                let key = key.clone();
                                div()
                                    .flex()
                                    .items_center()
                                    .gap_2()
                                    .when(i == 1, |el| el.child(text.clone()))
                                    .child(
                                        Self::button(
                                            SharedString::from(format!("step-{key}-{i}")),
                                            if i == 0 { "−" } else { "+" },
                                            false,
                                            disabled,
                                            p,
                                        )
                                        .on_click(
                                            cx.listener(move |this, _, _, cx| {
                                                if disabled {
                                                    return;
                                                }
                                                let number = ((current + direction * step)
                                                    .clamp(minimum, maximum)
                                                    * 1000.)
                                                    .round()
                                                    / 1000.;
                                                let value = if step.fract() == 0. {
                                                    json!(number as i64)
                                                } else {
                                                    json!(number)
                                                };
                                                this.command(
                                                    json!({"type":"set","key":key,"value":value}),
                                                    cx,
                                                );
                                            }),
                                        ),
                                    )
                            }),
                        )),
                );
            }
            "color" => {
                row = row.child(
                    Self::button(
                        SharedString::from(format!("color-{key}")),
                        label,
                        false,
                        false,
                        p,
                    )
                    .child(
                        div()
                            .size(px(14.))
                            .rounded_sm()
                            .bg(color(&self.state["preferences"][&key])),
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.command(json!({"type":"color","key":key}), cx)
                    })),
                );
            }
            "presets" => {
                row = row.child(
                    div().flex().gap_2().children(
                        ["Midnight", "Paper", "Cinema"]
                            .into_iter()
                            .enumerate()
                            .map(|(i, name)| {
                                Self::button(("preset", i), name, false, false, p).on_click(
                                    cx.listener(move |this, _, _, cx| {
                                        this.command(json!({"type":"preset","value":name}), cx)
                                    }),
                                )
                            }),
                    ),
                );
            }
            "root" => {
                row = row
                    .child(div().text_size(px(12.)).child(label))
                    .child(
                        div()
                            .text_size(px(10.))
                            .text_color(p.muted)
                            .overflow_hidden()
                            .child(string(&value)),
                    )
                    .child(
                        Self::button(
                            SharedString::from(format!("remove-{key}")),
                            "Remove folder",
                            false,
                            disabled,
                            p,
                        )
                        .on_click(cx.listener(move |this, _, _, cx| {
                            if !disabled {
                                this.command(json!({"type":"removeRoot","path":key}), cx);
                            }
                        })),
                    );
            }
            "action" | "edit" => {
                let edit = kind == "edit";
                row = row.child(
                    Self::button(
                        SharedString::from(format!("action-{key}")),
                        label,
                        false,
                        disabled,
                        p,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !disabled {
                            this.command(
                                if edit {
                                    json!({"type":"edit","key":key})
                                } else {
                                    json!({"type":key})
                                },
                                cx,
                            );
                        }
                    })),
                );
                if edit && value.as_str().is_some_and(|v| !v.is_empty()) {
                    row = row.child(
                        div()
                            .text_size(px(10.))
                            .text_color(p.muted)
                            .child(string(&value)),
                    );
                }
            }
            _ => row = row.child(label),
        }
        row.into_any_element()
    }
    fn inspector(&self, p: Palette, cx: &mut Context<Self>) -> Div {
        let sections = self.state["sections"][self.tab]
            .as_array()
            .cloned()
            .unwrap_or_default();
        div().w(px(340.)).h_full().flex_none().bg(p.panel).border_l_1().border_color(p.line).flex().flex_col()
   .on_drop::<ExternalPaths>(cx.listener(|this,paths: &ExternalPaths,_,cx|this.command(json!({"type":"dropRoots","paths":paths.paths().iter().map(|p|p.to_string_lossy().to_string()).collect::<Vec<_>>()}),cx)))
.child(div().p_4().flex().gap_1()
.children(["Style","Languages","Models"].into_iter().enumerate().map(|(i,title)|div().id(("tab",i)).flex_1().text_center().py_1p5().rounded_md().cursor_pointer().bg(if self.tab==i{p.button}else{p.panel}).font_weight(if self.tab==i{FontWeight::SEMIBOLD}else{FontWeight::NORMAL})
.child(title)
.on_click(cx.listener(move|this,_,_,cx|{this.tab=i;this.picker=None;cx.notify();})))))
.child(div().id(("inspector-scroll",self.tab)).flex_1().min_h_0().overflow_y_scroll().px_5().pb_4()
.children(sections.iter().map(|section|div().flex().flex_col().pt_4().pb_4().border_b_1().border_color(p.line)
.child(div().mb_2().font_weight(FontWeight::SEMIBOLD)
.child(string(&section["title"])))
.children(section["fields"].as_array().into_iter().flatten().map(|field|self.field(field,p,cx)))
.when(section["note"].as_str().is_some_and(|v|!v.is_empty()),|el|el
.child(div().pt_2().text_size(px(11.)).text_color(p.muted)
.child(string(&section["note"])))))))
    }
}
impl Render for CaptionFlow {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let fullscreen = window.is_fullscreen();
        if fullscreen && !self.was_fullscreen {
            self.fullscreen_inspector = false;
            self.picker = None;
        }
        self.was_fullscreen = fullscreen;
        let show_inspector = if fullscreen {
            self.fullscreen_inspector
        } else {
            self.inspector
        };
        let p = Palette::new(matches!(
            window.appearance(),
            WindowAppearance::Dark | WindowAppearance::VibrantDark
        ));
        let active = self.flag("active");
        let locked = self.flag("stopping") || self.flag("installing");
        let error = self.state["error"].as_str().unwrap_or("").to_owned();
        let notice = [
            self.state["notice"].as_str().unwrap_or(""),
            self.update_status.message.as_str(),
        ]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" · ");
        let canvas = self.render_canvas(window, fullscreen, show_inspector, cx);
        div().key_context("CaptionFlow").track_focus(&self.focus).size_full().flex().flex_col().overflow_hidden().bg(p.background).text_color(p.text).font_family(".SystemUIFont").text_size(px(13.))
.on_action(cx.listener(|this,_:&Listen,_,cx|this.listen(cx)))
.on_action(cx.listener(|this,_:&Preview,_,cx|this.command(json!({"type":"preview"}),cx)))
.on_action(cx.listener(|this,_:&CopyCaptions,_,cx|this.copy(cx)))
.on_action(cx.listener(|this,_:&Inspector,window,cx|this.toggle_inspector(window,cx)))
.on_action(cx.listener(|this,_:&Privacy,_,cx|this.command(json!({"type":"privacy"}),cx)))
.on_action(cx.listener(|this,_:&Settings,_,cx|{this.inspector=true;this.fullscreen_inspector=true;cx.notify();}))
.on_action(|_:&Fullscreen,window,_|window.toggle_fullscreen())
.on_action(cx.listener(|this,_:&Close,_,cx|this.request_quit(cx)))
.on_action(cx.listener(|this,_:&BackToLive,_,cx|this.back_to_live(None,cx)))
.on_action(cx.listener(|this,_:&Escape,window,cx|{if this.picker.take().is_some(){cx.notify();}else if window.is_fullscreen(){window.toggle_fullscreen();}}))
.when(!fullscreen,|el|el
.child(div().h(px(56.)).flex_none().flex().items_center().justify_between().pl(px(88.)).pr_4().border_b_1().border_color(p.line).on_mouse_down(MouseButton::Left,|_,window,_|window.start_window_move())
.child(div().font_weight(FontWeight::SEMIBOLD)
.child("CaptionFlow"))
.child(div().flex().gap_2().on_mouse_down(MouseButton::Left,|_,_,cx|cx.stop_propagation())
.child(Self::button("privacy","Privacy / sharing…",false,self.flag("installing"),p)
.on_click(cx.listener(|this,_,_,cx|{if !this.flag("installing"){this.command(json!({"type":"privacy"}),cx);}})))
.child(Self::button("fullscreen","Full screen",false,false,p)
.on_click(|_,window,_|window.toggle_fullscreen()))
.child(Self::button("inspector","Inspector",show_inspector,false,p)
.on_click(cx.listener(|this,_,window,cx|this.toggle_inspector(window,cx))))
.child(Self::button("listen",if active{"Stop"}else{"Start listening"},true,locked,p)
.on_click(cx.listener(|this,_,_,cx|this.listen(cx)))))))
.child(div().flex_1().min_h_0().flex()
.child(canvas)
.when(show_inspector,|el|el
.child(self.inspector(p,cx))))
.when(!fullscreen&&(!error.is_empty()||!notice.is_empty()),|el|el
.child(div().px_5().py_3().flex().items_center().gap_3().bg(p.panel)
.child(div().flex_1().min_w_0()
.child(if error.is_empty(){notice}else{error.clone()}))
.when(self.flag("capturePermissionIssue")&&!error.is_empty(),|el|el
.child(Self::button("capture-settings","Open System Settings",false,false,p)
.on_click(cx.listener(|this,_,_,cx|this.command(json!({"type":"captureSettings"}),cx)))))
.child(Self::button("dismiss","Dismiss",false,false,p)
.on_click(cx.listener(move|this,_,_,cx|this.command(json!({"type":if error.is_empty(){"dismissNotice"}else{"dismissError"}}),cx))))))
.when(!fullscreen,|el|el
.child(div().h(px(44.)).flex_none().px_5().flex().items_center().justify_between().border_t_1().border_color(p.line)
.child(div().text_size(px(11.)).text_color(p.muted)
.child(self.state["inputStatus"].as_str().unwrap_or("").to_owned()))
.child(div().flex().gap_2()
.child(Self::button("component-update", match self.update_status.phase.as_str() {
    "available" => "Download update", "ready" => "Restart to update", "checking" => "Checking…", "downloading" => "Downloading…", _ => "Check for updates"
}, false, !self.updates.online() || matches!(self.update_status.phase.as_str(), "checking" | "downloading"), p)
.on_click(cx.listener(|this,_,_,cx|this.update_app(cx))))
.child(Self::button("copy",if self.copied{"Copied"}else{"Copy captions"},false,false,p)
.on_click(cx.listener(|this,_,_,cx|this.copy(cx)))))))
    }
}
fn main() {
    if updater::run_helper() {
        return;
    }
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("warn")).init();
    Application::new().run(|cx: &mut App| {
        cx.bind_keys([
            KeyBinding::new("cmd-q", Quit, None),
            KeyBinding::new("cmd-w", Close, Some("CaptionFlow")),
            KeyBinding::new("cmd-r", Listen, Some("CaptionFlow")),
            KeyBinding::new("cmd-shift-p", Preview, Some("CaptionFlow")),
            KeyBinding::new("cmd-alt-i", Inspector, Some("CaptionFlow")),
            KeyBinding::new("ctrl-cmd-f", Fullscreen, Some("CaptionFlow")),
            KeyBinding::new("cmd-shift-c", CopyCaptions, Some("CaptionFlow")),
            KeyBinding::new("cmd-,", Settings, Some("CaptionFlow")),
            KeyBinding::new("escape", Escape, Some("CaptionFlow")),
            KeyBinding::new("cmd-down", BackToLive, Some("CaptionFlow")),
        ]);
        cx.set_menus(vec![
            Menu {
                name: "CaptionFlow".into(),
                items: vec![
                    MenuItem::action("Settings…", Settings),
                    MenuItem::action("Privacy / sharing…", Privacy),
                    MenuItem::separator(),
                    MenuItem::action("Quit CaptionFlow", Quit),
                ],
            },
            Menu {
                name: "Session".into(),
                items: vec![
                    MenuItem::action("Start / Stop Listening", Listen),
                    MenuItem::action("Preview Appearance", Preview),
                    MenuItem::separator(),
                    MenuItem::action("Copy Captions", CopyCaptions),
                ],
            },
            Menu {
                name: "View".into(),
                items: vec![
                    MenuItem::action("Toggle Inspector", Inspector),
                    MenuItem::action("Enter / Exit Full Screen", Fullscreen),
                    MenuItem::action("Back to Live", BackToLive),
                    MenuItem::action("Close Window", Close),
                ],
            },
        ]);
        let executable = std::env::current_exe().expect("app executable path");
        #[cfg(target_os = "macos")]
        let library = executable
            .parent()
            .unwrap()
            .join("../Frameworks/libCaptionFlowEngine.dylib");
        #[cfg(not(target_os = "macos"))]
        let library = executable.parent().unwrap().to_path_buf();
        let engine = Rc::new(Engine::load(&library).expect("load the bundled CaptionFlow engine"));
        let quit_engine = engine.clone();
        cx.on_action(move |_: &Quit, _| quit_engine.command(json!({"type":"quit"})));
        let smoke_test = std::env::args().any(|arg| arg == "--smoke-test");
        let bounds = Bounds::centered(None, size(px(1180.), px(760.)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                window_min_size: Some(size(px(900.), px(540.))),
                titlebar: Some(TitlebarOptions {
                    title: Some("CaptionFlow".into()),
                    appears_transparent: true,
                    traffic_light_position: Some(point(px(18.), px(22.))),
                }),
                ..Default::default()
            },
            |window, cx| {
                let view = cx.new(|cx| CaptionFlow::new(window, cx, engine));
                view.update(cx, |_, cx| CaptionFlow::start_polling(cx));
                if smoke_test {
                    view.update(cx, |this, cx| {
                        this.command(json!({"type":"preview"}), cx);
                        cx.spawn(async move |this, cx| {
                            cx.background_executor().timer(Duration::from_secs(2)).await;
                            this.update(cx, |this, cx| this.request_quit(cx)).ok();
                        })
                        .detach();
                    });
                }
                let handle = view.downgrade();
                window.on_window_should_close(cx, move |_, cx| {
                    handle.update(cx, |this, cx| this.request_quit(cx)).ok();
                    false
                });
                view
            },
        )
        .expect("open CaptionFlow window");
        cx.activate(true);
    });
}
