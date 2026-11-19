#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]
mod canvas;
mod captions;
mod controls;
mod engine;
mod focus;
mod updater;
use engine::Engine;
use gpui::{
    App, Application, Bounds, ClipboardItem, Context, Div, FocusHandle, Hsla, KeyBinding, Menu,
    MenuItem, MouseButton, SharedString, TitlebarOptions, Window, WindowAppearance, WindowBounds,
    WindowOptions, actions, div, point, prelude::*, px, rgb, size,
};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    rc::Rc,
    time::{Duration, Instant},
};
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
        BackToLive,
        ShowCaptions,
        ShowTranscript,
        ShowStyle,
        ShowLanguages,
        ShowModels,
        CheckForUpdates
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
    card: Hsla,
    control: Hsla,
    control_on: Hsla,
    menu: Hsla,
    menu_line: Hsla,
    accent: Hsla,
}
impl Palette {
    fn new(dark: bool) -> Self {
        let values = if dark {
            [
                0x25272b, 0x202226, 0xf2f3f5, 0xa9afb9, 0x3b3e44, 0x34373e, 0x2b2e33, 0x1b1d21,
                0x4a4e56, 0x2c2f35, 0x43474e, 0x2f6fe8,
            ]
        } else {
            [
                0xf5f6f8, 0xebeef2, 0x202631, 0x646e7c, 0xd9dde4, 0xffffff, 0xffffff, 0xe3e6eb,
                0xffffff, 0xfbfbfc, 0xd3d6dc, 0x2f6fe8,
            ]
        };
        let [
            background,
            panel,
            text,
            muted,
            line,
            button,
            card,
            control,
            control_on,
            menu,
            menu_line,
            accent,
        ] = values.map(|v| rgb(v).into());
        Self {
            background,
            panel,
            text,
            muted,
            line,
            button,
            card,
            control,
            control_on,
            menu,
            menu_line,
            accent,
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
    menu: Option<String>,
    menu_closed: Option<(String, Instant)>,
    settings_open: bool,
    pin_controls: bool,
    bar_hover: bool,
    controls_until: Option<Instant>,
    controls_shown: bool,
    caption_presentation: HashMap<&'static str, Value>,
    picker: Option<String>,
    picker_closed: Option<(String, Instant)>,
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
            menu: None,
            menu_closed: None,
            settings_open: false,
            pin_controls: false,
            bar_hover: false,
            controls_until: None,
            controls_shown: true,
            caption_presentation: HashMap::new(),
            picker: None,
            picker_closed: None,
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
                        if this.controls_shown != this.controls_visible() {
                            cx.notify();
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
            self.open_settings(cx);
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
}
impl Render for CaptionFlow {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let p = Palette::new(matches!(
            window.appearance(),
            WindowAppearance::Dark | WindowAppearance::VibrantDark
        ));
        let chrome = self.controls_visible();
        self.controls_shown = chrome;
        let canvas = self.render_canvas(window, chrome, cx);
        let toast = self.toast(p, cx);
        div()
            .key_context("CaptionFlow")
            .track_focus(&self.focus)
            .size_full()
            .relative()
            .overflow_hidden()
            .bg(p.background)
            .text_color(p.text)
            .font_family(".SystemUIFont")
            .text_size(px(13.))
            .on_mouse_move(cx.listener(|this, _, _, cx| this.reveal_controls(cx)))
            .on_action(cx.listener(|this, _: &Listen, _, cx| this.listen(cx)))
            .on_action(
                cx.listener(|this, _: &Preview, _, cx| this.command(json!({"type":"preview"}), cx)),
            )
            .on_action(cx.listener(|this, _: &CopyCaptions, _, cx| this.copy(cx)))
            .on_action(cx.listener(|this, _: &Inspector, _, cx| {
                this.pin_controls = !this.pin_controls;
                cx.notify();
            }))
            .on_action(
                cx.listener(|this, _: &Privacy, _, cx| this.command(json!({"type":"privacy"}), cx)),
            )
            .on_action(cx.listener(|this, _: &Settings, _, cx| this.open_settings(cx)))
            .on_action(|_: &Fullscreen, window, _| window.toggle_fullscreen())
            .on_action(cx.listener(|this, _: &Close, _, cx| this.request_quit(cx)))
            .on_action(cx.listener(|this, _: &BackToLive, _, cx| this.back_to_live(None, cx)))
            .on_action(cx.listener(|this, _: &ShowCaptions, _, cx| this.set_layout(false, cx)))
            .on_action(cx.listener(|this, _: &ShowTranscript, _, cx| this.set_layout(true, cx)))
            .on_action(cx.listener(|this, _: &ShowStyle, _, cx| this.open_menu("style", cx)))
            .on_action(
                cx.listener(|this, _: &ShowLanguages, _, cx| this.open_menu("listening", cx)),
            )
            .on_action(cx.listener(|this, _: &ShowModels, _, cx| this.open_settings(cx)))
            .on_action(cx.listener(|this, _: &CheckForUpdates, _, cx| this.update_app(cx)))
            .on_action(cx.listener(|this, _: &Escape, window, cx| this.handle_escape(window, cx)))
            .child(canvas)
            // The title bar is transparent; keep the window draggable from its top edge.
            .child(
                div()
                    .absolute()
                    .top_0()
                    .left_0()
                    .right_0()
                    .h(px(44.))
                    .on_mouse_down(MouseButton::Left, |_, window, _| window.start_window_move()),
            )
            .children(toast)
            .when(chrome, |el| {
                el.child(
                    div()
                        .absolute()
                        .bottom(px(26.))
                        .left_0()
                        .right_0()
                        .flex()
                        .justify_center()
                        .child(self.control_bar(p, cx)),
                )
            })
            .when(self.settings_open, |el| {
                el.child(self.settings_sheet(p, cx))
            })
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
            KeyBinding::new("cmd-1", ShowCaptions, Some("CaptionFlow")),
            KeyBinding::new("cmd-2", ShowTranscript, Some("CaptionFlow")),
            KeyBinding::new("cmd-t", ShowStyle, Some("CaptionFlow")),
            KeyBinding::new("cmd-l", ShowLanguages, Some("CaptionFlow")),
        ]);
        cx.set_menus(vec![
            Menu {
                name: "CaptionFlow".into(),
                items: vec![
                    MenuItem::action("Settings…", Settings),
                    MenuItem::action("Privacy & Sharing…", Privacy),
                    MenuItem::action("Check for Updates…", CheckForUpdates),
                    MenuItem::separator(),
                    MenuItem::action("Quit CaptionFlow", Quit),
                ],
            },
            Menu {
                name: "Session".into(),
                items: vec![
                    MenuItem::action("Start or Stop Listening", Listen),
                    MenuItem::action("Preview Appearance", Preview),
                    MenuItem::separator(),
                    MenuItem::action("Copy Captions", CopyCaptions),
                    MenuItem::action("Back to Live", BackToLive),
                ],
            },
            Menu {
                name: "View".into(),
                items: vec![
                    MenuItem::action("Captions", ShowCaptions),
                    MenuItem::action("Transcript", ShowTranscript),
                    MenuItem::separator(),
                    MenuItem::action("Caption Style…", ShowStyle),
                    MenuItem::action("Input & Language…", ShowLanguages),
                    MenuItem::action("Models…", ShowModels),
                    MenuItem::separator(),
                    MenuItem::action("Keep Controls Visible", Inspector),
                    MenuItem::action("Enter or Exit Full Screen", Fullscreen),
                ],
            },
            Menu {
                name: "Window".into(),
                items: vec![MenuItem::action("Close Window", Close)],
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
