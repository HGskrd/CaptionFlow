#[cfg(target_os = "macos")]
mod native {
    use libloading::Library;
    use serde_json::Value;
    use std::{
        ffi::{CStr, CString, c_char},
        mem::ManuallyDrop,
        path::Path,
    };

    // The Swift engine is main-thread confined, as are all callers in this module.
    // Keep the library alive for every stored function pointer and pending Swift task.
    pub struct Engine {
        // AppKit retains Swift delegate methods and signal handlers until process exit.
        // Unloading this library when the last GPUI view drops would invalidate them.
        _library: ManuallyDrop<Library>,
        command: unsafe extern "C" fn(*const c_char),
        snapshot: unsafe extern "C" fn() -> *mut c_char,
        free: unsafe extern "C" fn(*mut c_char),
    }
    impl Engine {
        pub fn load(path: &Path) -> Result<Self, Box<dyn std::error::Error>> {
            // SAFETY: this app builds and signs the library alongside its executable.
            // The C ABI is defined in native/GPUIEngine.swift and called on AppKit's thread.
            unsafe {
                let library = Library::new(path)?;
                let initialize =
                    *library.get::<unsafe extern "C" fn()>(b"captionflow_initialize\0")?;
                let command = *library.get(b"captionflow_command\0")?;
                let snapshot = *library.get(b"captionflow_snapshot\0")?;
                let free = *library.get(b"captionflow_free\0")?;
                initialize();
                Ok(Self {
                    _library: ManuallyDrop::new(library),
                    command,
                    snapshot,
                    free,
                })
            }
        }
        pub fn command(&self, command: Value) {
            let json = CString::new(command.to_string()).expect("JSON contains no raw NUL");
            // SAFETY: the string is valid throughout this synchronous main-thread call.
            unsafe {
                (self.command)(json.as_ptr());
            }
        }
        pub fn poll(&self) -> Option<Value> {
            // SAFETY: Swift returns either null or a strdup allocation; copy and release
            // it exactly once, including when decoding fails. Calls stay on the main thread.
            unsafe {
                let pointer = (self.snapshot)();
                if pointer.is_null() {
                    return None;
                }
                let result = serde_json::from_slice(CStr::from_ptr(pointer).to_bytes()).ok();
                (self.free)(pointer);
                result
            }
        }
    }
}
#[cfg(target_os = "macos")]
pub use native::Engine;

#[cfg(not(target_os = "macos"))]
mod portable {
    use serde_json::{Value, json};
    use std::{
        io::{BufRead, BufReader, Write},
        path::Path,
        process::{Child, ChildStdin, Command, Stdio},
        sync::{Arc, Mutex},
    };
    pub struct Engine {
        child: Mutex<Child>,
        input: Mutex<ChildStdin>,
        latest: Arc<Mutex<Option<Value>>>,
    }
    impl Engine {
        pub fn load(root: &Path) -> Result<Self, Box<dyn std::error::Error>> {
            let log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(std::env::temp_dir().join("CaptionFlow-engine.log"))?;
            #[cfg(target_os = "linux")]
            let python = std::env::var_os("CAPTIONFLOW_PYTHON")
                .ok_or("Launch CaptionFlow using start-captionflow.sh after setup")?;
            #[cfg(not(target_os = "linux"))]
            let python = root.join("python/python.exe");
            let mut command = Command::new(python);
            command
                .arg("-u")
                .arg(root.join("portable/gpui_engine.py"))
                .env("PYTHONDONTWRITEBYTECODE", "1")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(log);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                command.creation_flags(0x08000000);
            }
            let mut child = command.spawn()?;
            let input = child.stdin.take().ok_or("engine stdin")?;
            let output = child.stdout.take().ok_or("engine stdout")?;
            let latest = Arc::new(Mutex::new(None));
            let receiver = latest.clone();
            std::thread::spawn(move || {
                for line in BufReader::new(output).lines() {
                    let Ok(line) = line else { break };
                    if let Ok(value) = serde_json::from_str(&line) {
                        *receiver.lock().unwrap() = Some(value);
                    }
                }
                let mut state = receiver.lock().unwrap();
                let value = state.get_or_insert_with(|| json!({}));
                value["error"] = json!(
                    "The caption engine stopped. Restart CaptionFlow; diagnostics are in CaptionFlow-engine.log."
                );
                value["shutdownComplete"] = json!(true);
            });
            Ok(Self {
                child: Mutex::new(child),
                input: Mutex::new(input),
                latest,
            })
        }
        pub fn command(&self, command: Value) {
            let _ = writeln!(self.input.lock().unwrap(), "{command}");
        }
        pub fn poll(&self) -> Option<Value> {
            self.latest.lock().unwrap().take()
        }
    }
    impl Drop for Engine {
        fn drop(&mut self) {
            if self
                .child
                .lock()
                .unwrap()
                .try_wait()
                .ok()
                .flatten()
                .is_none()
            {
                let _ = self.child.lock().unwrap().kill();
                let _ = self.child.lock().unwrap().wait();
            }
        }
    }
}
#[cfg(not(target_os = "macos"))]
pub use portable::Engine;
