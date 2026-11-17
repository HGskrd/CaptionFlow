//! Signed component updates. Stage a complete verified installation beside the
//! current one, drain capture, then let a separate process swap and relaunch it.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    fs::{self, File},
    io::{Read, Write},
    path::{Component as PathComponent, Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
const PUBLIC_KEY: &str = "yTp27s/gWvh5/qBk/I6/IcszfL0oPFANbj1ffM+QDiQ=";
const ORIGIN: &str = "https://h.gskrd.me/apps/captionflow/releases/";
const METADATA: &str = include_str!("../../Sources/CaptionFlow/Resources/worker/release.json");
#[derive(Clone, Debug, Deserialize, Serialize)]
struct Manifest {
    schema: u32,
    platform: String,
    version: String,
    sequence: u64,
    expires: u64,
    components: BTreeMap<String, Component>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
struct Component {
    url: String,
    sha256: String,
    size: u64,
    files: Vec<Entry>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
struct Entry {
    path: String,
    sha256: String,
    size: u64,
    mode: u32,
    #[serde(default)]
    symlink: Option<String>,
}
#[derive(Clone, Default, PartialEq)]
pub struct Status {
    pub phase: String,
    pub message: String,
    pub version: String,
    pub bytes: u64,
    envelope: Option<String>,
    stage: Option<PathBuf>,
}
pub struct Updates {
    state: Arc<Mutex<Status>>,
    online: Arc<AtomicBool>,
    root: PathBuf,
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
fn platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows-x86_64"
    } else if cfg!(target_os = "linux") {
        "linux-x86_64"
    } else if cfg!(target_arch = "aarch64") {
        "macos-aarch64"
    } else {
        "macos-x86_64"
    }
}
fn sequence() -> u64 {
    serde_json::from_str::<serde_json::Value>(METADATA).unwrap()["sequence"]
        .as_u64()
        .unwrap()
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn file_digest(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 1024 * 128];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn safe_path(value: &str) -> Result<PathBuf> {
    if value.is_empty()
        || value.contains(['\\', ':'])
        || value.starts_with('/')
        || value
            .split('/')
            .any(|s| s.is_empty() || s == "." || s == "..")
    {
        return Err("Invalid update path".into());
    }
    let path = PathBuf::from(value);
    if path
        .components()
        .any(|c| !matches!(c, PathComponent::Normal(_)))
    {
        return Err("Invalid update path".into());
    }
    Ok(path)
}
fn valid_hash(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn decode(envelope: &str, target: &str, minimum: u64, key: &str) -> Result<Manifest> {
    let value: serde_json::Value = serde_json::from_str(envelope)?;
    let payload = STANDARD.decode(value["payload"].as_str().ok_or("Missing payload")?)?;
    let signature = Signature::from_slice(
        &STANDARD.decode(value["signature"].as_str().ok_or("Missing signature")?)?,
    )?;
    let bytes: [u8; 32] = STANDARD
        .decode(key)?
        .try_into()
        .map_err(|_| "Invalid public key")?;
    VerifyingKey::from_bytes(&bytes)?.verify_strict(&payload, &signature)?;
    let manifest: Manifest = serde_json::from_slice(&payload)?;
    if manifest.schema != 2
        || manifest.platform != target
        || manifest.sequence <= minimum
        || manifest.expires <= now()
        || manifest.components.is_empty()
    {
        return Err("Update version, platform or expiration is invalid".into());
    }
    let mut paths = HashSet::new();
    let mut links = Vec::new();
    for (name, component) in &manifest.components {
        safe_path(name)?;
        if !["app", "worker", "python", "runtimes"].contains(&name.as_str())
            || component.files.is_empty()
            || !valid_hash(&component.sha256)
            || component.size == 0
            || component.size > 8 * 1024 * 1024 * 1024
        {
            return Err("Invalid update component".into());
        }
        let prefix = format!("{ORIGIN}CaptionFlow-{}-", manifest.version);
        let filename = component
            .url
            .strip_prefix(&prefix)
            .ok_or("Update outside the release origin")?;
        if !filename.ends_with(".zip") || filename.contains(['/', '?', '#', '%']) {
            return Err("Invalid component URL".into());
        }
        for entry in &component.files {
            let path = safe_path(&entry.path)?;
            if !paths.insert(entry.path.to_lowercase())
                || !valid_hash(&entry.sha256)
                || entry.size > 8 * 1024 * 1024 * 1024
                || entry.mode & !0o777 != 0
            {
                return Err("Invalid or duplicate update file".into());
            }
            if let Some(target) = &entry.symlink {
                if cfg!(windows) || target.contains(['\\', ':']) || Path::new(target).is_absolute()
                {
                    return Err("Unsafe update symlink".into());
                }
                let mut depth = path.parent().unwrap().components().count();
                for piece in Path::new(target).components() {
                    match piece {
                        PathComponent::ParentDir => {
                            if depth == 0 {
                                return Err("Escaping symlink".into());
                            }
                            depth -= 1;
                        }
                        PathComponent::Normal(_) => depth += 1,
                        PathComponent::CurDir => {}
                        _ => return Err("Unsafe symlink".into()),
                    }
                }
                if entry.size != target.len() as u64 || entry.sha256 != digest(target.as_bytes()) {
                    return Err("Invalid symlink digest".into());
                }
                links.push(PathBuf::from(path.to_string_lossy().to_lowercase()));
            }
        }
    }
    for path in &paths {
        if links
            .iter()
            .any(|link| Path::new(path).starts_with(link) && Path::new(path) != link)
        {
            return Err("File nested beneath a symlink".into());
        }
    }
    Ok(manifest)
}
fn matches(root: &Path, entry: &Entry) -> bool {
    let path = root.join(&entry.path);
    let Ok(info) = fs::symlink_metadata(&path) else {
        return false;
    };
    if let Some(link) = &entry.symlink {
        return info.file_type().is_symlink()
            && fs::read_link(path).ok().as_deref() == Some(Path::new(link));
    }
    if !info.is_file() || info.len() != entry.size {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if info.permissions().mode() & 0o777 != entry.mode {
            return false;
        }
    }
    file_digest(&path).is_ok_and(|hash| hash == entry.sha256)
}
fn check_consent(online: &AtomicBool) -> Result<()> {
    if online.load(Ordering::SeqCst) {
        Ok(())
    } else {
        Err("Update cancelled: local-only mode is enabled".into())
    }
}
fn agent() -> ureq::Agent {
    ureq::Agent::new_with_config(
        ureq::Agent::config_builder()
            .https_only(true)
            .timeout_connect(Some(Duration::from_secs(15)))
            .timeout_global(Some(Duration::from_secs(20 * 60)))
            .build(),
    )
}
fn fetch(url: &str, destination: &Path, size: u64, hash: &str, online: &AtomicBool) -> Result<()> {
    check_consent(online)?;
    if destination.is_file()
        && fs::metadata(destination)?.len() == size
        && file_digest(destination)? == hash
    {
        return Ok(());
    }
    let part = destination.with_extension("part");
    let outcome = (|| -> Result<()> {
        let mut reader = agent()
            .get(url)
            .header("User-Agent", "CaptionFlow-Updater")
            .call()?
            .into_body()
            .into_reader();
        let mut file = File::create(&part)?;
        let mut count = 0;
        let mut buffer = [0; 128 * 1024];
        loop {
            check_consent(online)?;
            let n = reader.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            count += n as u64;
            if count > size {
                return Err("Oversized component download".into());
            }
            file.write_all(&buffer[..n])?;
        }
        file.sync_all()?;
        if count != size || file_digest(&part)? != hash {
            return Err("Component checksum mismatch".into());
        }
        fs::rename(&part, destination)?;
        Ok(())
    })();
    if outcome.is_err() {
        let _ = fs::remove_file(part);
    }
    outcome
}
fn write_entry(root: &Path, entry: &Entry, source: impl Read) -> Result<()> {
    let path = root.join(safe_path(&entry.path)?);
    fs::create_dir_all(path.parent().unwrap())?;
    if let Some(target) = &entry.symlink {
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, path)?;
        #[cfg(not(unix))]
        {
            let _ = target;
            return Err("Unsupported symlink".into());
        }
    } else {
        let mut file = File::create(&path)?;
        let count = std::io::copy(&mut source.take(entry.size + 1), &mut file)?;
        if count != entry.size {
            return Err("Wrong update file length".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(entry.mode))?;
        }
    }
    Ok(())
}
fn unpack(archive: &Path, root: &Path, component: &Component) -> Result<()> {
    let mut zip = zip::ZipArchive::new(File::open(archive)?)?;
    if zip.len() != component.files.len() {
        return Err("Unexpected files in component".into());
    }
    let mut seen = HashSet::new();
    for index in 0..zip.len() {
        let mut member = zip.by_index(index)?;
        let entry = component
            .files
            .iter()
            .find(|entry| entry.path == member.name())
            .ok_or("Unknown archive member")?;
        if !seen.insert(entry.path.clone()) || member.size() != entry.size {
            return Err("Invalid archive member".into());
        }
        if let Some(link) = &entry.symlink {
            let mut value = Vec::new();
            (&mut member).take(entry.size + 1).read_to_end(&mut value)?;
            if value != link.as_bytes() {
                return Err("Wrong symlink target".into());
            }
        }
        write_entry(root, entry, member)?;
    }
    Ok(())
}
fn verify_tree(root: &Path, manifest: &Manifest) -> Result<()> {
    for component in manifest.components.values() {
        for entry in &component.files {
            if !matches(root, entry) {
                return Err(format!("Update verification failed: {}", entry.path).into());
            }
        }
    }
    #[cfg(target_os = "macos")]
    if !Command::new("/usr/bin/codesign")
        .args(["--verify", "--deep", "--strict"])
        .arg(root)
        .status()?
        .success()
    {
        return Err("Updated app signature is invalid".into());
    }
    Ok(())
}
fn app_root() -> Result<PathBuf> {
    let executable = std::env::current_exe()?;
    #[cfg(target_os = "macos")]
    {
        Ok(executable
            .parent()
            .and_then(Path::parent)
            .and_then(Path::parent)
            .ok_or("Not an app bundle")?
            .to_path_buf())
    }
    #[cfg(not(target_os = "macos"))]
    {
        Ok(executable
            .parent()
            .ok_or("Missing install folder")?
            .to_path_buf())
    }
}
impl Updates {
    pub fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(Status::default())),
            online: Arc::new(AtomicBool::new(false)),
            root: app_root().expect("application path"),
        }
    }
    pub fn status(&self) -> Status {
        self.state.lock().unwrap().clone()
    }
    pub fn online(&self) -> bool {
        self.online.load(Ordering::SeqCst)
    }
    pub fn set_online(&self, value: bool) {
        let previous = self.online.swap(value, Ordering::SeqCst);
        if value && !previous {
            self.check();
        }
    }
    pub fn check(&self) {
        if cfg!(target_os = "linux") {
            let mut state = self.state.lock().unwrap();
            state.phase = "current".into();
            state.message = "Linux preview updates are available on GitHub Releases".into();
            return;
        }
        if !self.online() {
            return;
        }
        {
            let mut state = self.state.lock().unwrap();
            if ["checking", "downloading", "ready"].contains(&state.phase.as_str()) {
                return;
            }
            state.phase = "checking".into();
            state.message = "Checking for updates…".into();
        }
        let state = self.state.clone();
        let online = self.online.clone();
        let root = self.root.clone();
        std::thread::spawn(move || {
            let result = (|| -> Result<(String, Manifest, u64)> {
                check_consent(&online)?;
                let url = format!(
                    "https://h.gskrd.me/apps/captionflow/v2/releases/{}",
                    platform()
                );
                let mut reader = agent()
                    .get(url)
                    .header("User-Agent", "CaptionFlow-Updater")
                    .call()?
                    .into_body()
                    .into_reader()
                    .take(16 * 1024 * 1024 + 1);
                let mut envelope = String::new();
                reader.read_to_string(&mut envelope)?;
                if envelope.len() > 16 * 1024 * 1024 {
                    return Err("Oversized update manifest".into());
                }
                let value: serde_json::Value = serde_json::from_str(&envelope)?;
                // Verify even an equal version before reporting that it is current.
                let payload =
                    STANDARD.decode(value["payload"].as_str().ok_or("Missing payload")?)?;
                let candidate: Manifest = serde_json::from_slice(&payload)?;
                let manifest = decode(
                    &envelope,
                    platform(),
                    sequence().saturating_sub(1),
                    PUBLIC_KEY,
                )?;
                if candidate.sequence == sequence() {
                    return Ok((envelope, manifest, 0));
                }
                let bytes = manifest
                    .components
                    .values()
                    .filter(|c| !c.files.iter().all(|e| matches(&root, e)))
                    .map(|c| c.size)
                    .sum();
                Ok((envelope, manifest, bytes))
            })();
            let mut status = state.lock().unwrap();
            if !online.load(Ordering::SeqCst) {
                *status = Status::default();
                return;
            }
            match result {
                Ok((envelope, manifest, bytes)) if manifest.sequence > sequence() => {
                    status.phase = "available".into();
                    status.message = format!(
                        "CaptionFlow {} is available · {:.1} MB to download",
                        manifest.version,
                        bytes as f64 / 1e6
                    );
                    status.version = manifest.version;
                    status.bytes = bytes;
                    status.envelope = Some(envelope);
                }
                Ok(_) => {
                    status.phase = "current".into();
                    status.message = "CaptionFlow is up to date.".into();
                }
                Err(error) => {
                    status.phase = "error".into();
                    status.message = format!("Update check unavailable: {error}");
                }
            }
        });
    }
    pub fn download(&self) {
        if !self.online() {
            return;
        }
        let envelope;
        {
            let mut state = self.state.lock().unwrap();
            if state.phase != "available" {
                return;
            }
            envelope = state.envelope.clone().unwrap();
            state.phase = "downloading".into();
            state.message = "Downloading changed components…".into();
        }
        let state = self.state.clone();
        let online = self.online.clone();
        let root = self.root.clone();
        std::thread::spawn(move || {
            let result = (|| -> Result<PathBuf> {
                let manifest = decode(&envelope, platform(), sequence(), PUBLIC_KEY)?;
                let token = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
                let stage = root
                    .parent()
                    .ok_or("Missing application parent")?
                    .join(format!(".CaptionFlow-update-{token}"));
                fs::create_dir(&stage)?;
                let outcome = (|| -> Result<()> {
                    let app = stage.join(root.file_name().ok_or("Missing application name")?);
                    fs::create_dir(&app)?;
                    for (name, component) in &manifest.components {
                        check_consent(&online)?;
                        state.lock().unwrap().message = format!("Preparing {name}…");
                        if component.files.iter().all(|entry| matches(&root, entry)) {
                            for entry in &component.files {
                                check_consent(&online)?;
                                if entry.symlink.is_none() {
                                    write_entry(&app, entry, File::open(root.join(&entry.path))?)?;
                                } else {
                                    write_entry(&app, entry, std::io::empty())?;
                                }
                            }
                        } else {
                            let archive = stage.join(format!("{}.zip", component.sha256));
                            fetch(
                                &component.url,
                                &archive,
                                component.size,
                                &component.sha256,
                                &online,
                            )?;
                            unpack(&archive, &app, component)?;
                            fs::remove_file(archive)?;
                        }
                    }
                    verify_tree(&app, &manifest)?;
                    check_consent(&online)?;
                    fs::write(stage.join("manifest.json"), &envelope)?;
                    Ok(())
                })();
                if let Err(error) = outcome {
                    let _ = fs::remove_dir_all(&stage);
                    return Err(error);
                }
                Ok(stage)
            })();
            let mut status = state.lock().unwrap();
            match result {
                Ok(stage) => {
                    status.phase = "ready".into();
                    status.message =
                        "Update verified. Restart to install; your models and settings are kept."
                            .into();
                    status.stage = Some(stage);
                }
                Err(error) => {
                    status.phase = "error".into();
                    status.message = format!("Update stopped: {error}");
                }
            }
        });
    }
    pub fn launch_helper(&self) -> Result<()> {
        check_consent(&self.online)?;
        let status = self.status();
        let stage = status.stage.ok_or("No verified update is ready")?;
        let helper = stage.join(if cfg!(windows) {
            "CaptionFlowUpdater.exe"
        } else {
            "CaptionFlowUpdater"
        });
        fs::copy(std::env::current_exe()?, &helper)?;
        let mut command = Command::new(helper);
        command
            .arg("--apply-update")
            .arg(&stage)
            .arg(&self.root)
            .arg(std::process::id().to_string());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        command.spawn()?;
        Ok(())
    }
    pub fn failed(&self, message: String) {
        let mut state = self.state.lock().unwrap();
        state.phase = "error".into();
        state.message = message;
    }
}
#[cfg(unix)]
fn process_exists(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}
#[cfg(windows)]
fn process_exists(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::Threading::{GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut code = 0;
        let ok = GetExitCodeProcess(handle, &mut code);
        CloseHandle(handle);
        ok != 0 && code == 259
    }
}
fn relaunch(root: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        if !Command::new("/usr/bin/open")
            .arg("-n")
            .arg(root)
            .status()?
            .success()
        {
            return Err("Could not relaunch app".into());
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        Command::new(root.join(if cfg!(target_os = "linux") {
            "start-captionflow.sh"
        } else {
            "CaptionFlowGPUI.exe"
        }))
        .spawn()?;
    }
    Ok(())
}
fn leave_installation_directory(stage: &Path) -> Result<()> {
    // Windows locks a process's current directory against renaming. The helper
    // can inherit the install directory from a shortcut or portable launcher.
    // This runs before GPUI or any other threads are started in the helper.
    std::env::set_current_dir(stage)?;
    Ok(())
}
#[cfg(windows)]
fn preserve_uninstaller(root: &Path, app: &Path) -> Result<()> {
    // NSIS creates this at install time, so it is absent from the component
    // archives. Keep the installed uninstaller when replacing the app tree.
    let existing = root.join("Uninstall.exe");
    let destination = app.join("Uninstall.exe");
    if !destination.exists() {
        match fs::symlink_metadata(&existing) {
            Ok(info) if info.file_type().is_file() => {
                fs::copy(existing, destination)?;
            }
            Ok(_) => return Err("The installed uninstaller is not a regular file".into()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}
fn retire_installation(root: &Path, backup: &Path) -> std::io::Result<()> {
    for attempt in 0..50 {
        match fs::rename(root, backup) {
            Ok(()) => return Ok(()),
            Err(error)
                if cfg!(windows)
                    && matches!(error.raw_os_error(), Some(5 | 32 | 33))
                    && attempt < 49 =>
            {
                // The caption engine or a scanner may still be closing files.
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!()
}
pub fn run_helper() -> bool {
    let args = std::env::args_os().collect::<Vec<_>>();
    if args.get(1).is_none_or(|a| a != "--apply-update") {
        return false;
    }
    let result = (|| -> Result<()> {
        if args.len() != 5 {
            return Err("Invalid updater arguments".into());
        }
        let stage = PathBuf::from(&args[2]).canonicalize()?;
        let root = PathBuf::from(&args[3]).canonicalize()?;
        if stage.parent() != root.parent()
            || !stage
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with(".CaptionFlow-update-")
        {
            return Err("Invalid staging directory".into());
        }
        leave_installation_directory(&stage)?;
        let pid: u32 = args[4].to_string_lossy().parse()?;
        for _ in 0..1200 {
            if !process_exists(pid) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if process_exists(pid) {
            return Err("CaptionFlow did not finish shutting down".into());
        }
        let manifest = decode(
            &fs::read_to_string(stage.join("manifest.json"))?,
            platform(),
            sequence(),
            PUBLIC_KEY,
        )?;
        let app = stage.join(root.file_name().unwrap());
        verify_tree(&app, &manifest)?;
        #[cfg(windows)]
        preserve_uninstaller(&root, &app)?;
        let backup = stage.join("previous");
        retire_installation(&root, &backup)?;
        if let Err(error) = fs::rename(&app, &root) {
            fs::rename(&backup, &root)?;
            return Err(error.into());
        }
        if let Err(error) = relaunch(&root) {
            let _ = fs::rename(&root, &app);
            fs::rename(&backup, &root)?;
            let _ = relaunch(&root);
            return Err(error);
        }
        // Retain the previous installation for recovery until a later launch
        // has acknowledged the replacement.
        fs::write(
            stage.join("installed.json"),
            serde_json::to_vec(
                &serde_json::json!({"version":manifest.version,"sequence":manifest.sequence,"root":root}),
            )?,
        )?;
        Ok(())
    })();
    if let Err(error) = result {
        let path = args
            .get(2)
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let _ = fs::write(path.join("update-error.txt"), error.to_string());
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    #[cfg(windows)]
    #[test]
    fn helper_releases_inherited_install_directory_lock() {
        const CHILD: &str = "CAPTIONFLOW_DIRECTORY_LOCK_TEST";
        if let Some(directory) = std::env::var_os(CHILD) {
            let base = PathBuf::from(directory);
            let root = base.join("CaptionFlow");
            let stage = base.join(".CaptionFlow-update-test");
            assert_eq!(
                std::env::current_dir().unwrap().canonicalize().unwrap(),
                root.canonicalize().unwrap()
            );
            assert!(fs::rename(&root, stage.join("locked")).is_err());
            leave_installation_directory(&stage).unwrap();
            preserve_uninstaller(&root, &stage.join("CaptionFlow")).unwrap();
            retire_installation(&root, &stage.join("previous")).unwrap();
            fs::rename(stage.join("CaptionFlow"), &root).unwrap();
            assert_eq!(fs::read(root.join("version")).unwrap(), b"new");
            assert_eq!(
                fs::read(root.join("Uninstall.exe")).unwrap(),
                b"installed uninstaller"
            );
            assert_eq!(fs::read(stage.join("previous/version")).unwrap(), b"old");
            return;
        }
        let base =
            std::env::temp_dir().join(format!("captionflow-lock-test-{}", std::process::id()));
        let root = base.join("CaptionFlow");
        let stage = base.join(".CaptionFlow-update-test");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir_all(stage.join("CaptionFlow")).unwrap();
        fs::write(root.join("version"), b"old").unwrap();
        fs::write(root.join("Uninstall.exe"), b"installed uninstaller").unwrap();
        fs::write(stage.join("CaptionFlow/version"), b"new").unwrap();
        let status = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "updater::tests::helper_releases_inherited_install_directory_lock",
                "--nocapture",
            ])
            .env(CHILD, &base)
            .current_dir(&root)
            .status()
            .unwrap();
        assert!(status.success());
        fs::remove_dir_all(base).unwrap();
    }
    fn fixture() -> (SigningKey, Manifest) {
        let key = SigningKey::from_bytes(&[17; 32]);
        let entry = Entry {
            path: "Contents/worker.py".into(),
            sha256: digest(b"new worker"),
            size: 10,
            mode: 0o644,
            symlink: None,
        };
        let component = Component {
            url: format!("{ORIGIN}CaptionFlow-9.0.0-worker.zip"),
            sha256: "a".repeat(64),
            size: 100,
            files: vec![entry],
        };
        (
            key,
            Manifest {
                schema: 2,
                platform: platform().into(),
                version: "9.0.0".into(),
                sequence: sequence() + 1,
                expires: now() + 3600,
                components: BTreeMap::from([("worker".into(), component)]),
            },
        )
    }
    fn envelope(key: &SigningKey, manifest: &Manifest) -> String {
        let payload = serde_json::to_vec(manifest).unwrap();
        serde_json::json!({"payload":STANDARD.encode(&payload),"signature":STANDARD.encode(key.sign(&payload).to_bytes())}).to_string()
    }
    fn accept(key: &SigningKey, manifest: &Manifest) -> bool {
        decode(
            &envelope(key, manifest),
            platform(),
            sequence(),
            &STANDARD.encode(key.verifying_key().to_bytes()),
        )
        .is_ok()
    }
    #[test]
    fn signed_updates_reject_tampering_replay_wrong_platform_and_expiration() {
        let (key, mut manifest) = fixture();
        assert!(accept(&key, &manifest));
        let signed = envelope(&key, &manifest).replace("signature", "bad-signature");
        assert!(decode(&signed, platform(), sequence(), PUBLIC_KEY).is_err());
        manifest.sequence = sequence();
        assert!(!accept(&key, &manifest));
        manifest.sequence += 1;
        manifest.platform = "wrong".into();
        assert!(!accept(&key, &manifest));
        manifest.platform = platform().into();
        manifest.expires = now() - 1;
        assert!(!accept(&key, &manifest));
        manifest.expires = now() + 3600;
        manifest.components.get_mut("worker").unwrap().url =
            "https://example.org/worker.zip".into();
        assert!(!accept(&key, &manifest));
    }
    #[test]
    fn update_paths_reject_traversal_duplicates_and_symlink_ancestors() {
        let (key, mut manifest) = fixture();
        for bad in [
            "../outside",
            "/absolute",
            "C:/outside",
            "worker/../../outside",
            "worker\\file",
            "worker//file",
            "worker/./file",
        ] {
            manifest.components.get_mut("worker").unwrap().files[0].path = bad.into();
            assert!(!accept(&key, &manifest), "accepted {bad}");
        }
        let (_, mut manifest) = fixture();
        let mut duplicate = manifest.components["worker"].files[0].clone();
        duplicate.path = duplicate.path.to_uppercase();
        manifest
            .components
            .get_mut("worker")
            .unwrap()
            .files
            .push(duplicate);
        assert!(!accept(&key, &manifest));
        let (_, mut manifest) = fixture();
        let entry = &mut manifest.components.get_mut("worker").unwrap().files[0];
        entry.symlink = Some("../../escape".into());
        entry.size = 12;
        entry.sha256 = digest(b"../../escape");
        assert!(!accept(&key, &manifest));
    }
    #[test]
    fn unchanged_component_files_are_reused_and_stale_files_are_excluded() {
        let (_, manifest) = fixture();
        let root = std::env::temp_dir().join(format!(
            "captionflow-update-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let installed = root.join("installed");
        let staged = root.join("staged");
        fs::create_dir_all(installed.join("Contents")).unwrap();
        fs::create_dir_all(&staged).unwrap();
        let entry = &manifest.components["worker"].files[0];
        write_entry(&installed, entry, &b"new worker"[..]).unwrap();
        fs::write(installed.join("obsolete"), b"old").unwrap();
        assert!(matches(&installed, entry));
        write_entry(
            &staged,
            entry,
            File::open(installed.join(&entry.path)).unwrap(),
        )
        .unwrap();
        assert!(matches(&staged, entry));
        assert!(!staged.join("obsolete").exists());
        fs::write(installed.join(&entry.path), b"corruption").unwrap();
        assert!(!matches(&installed, entry));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn archive_unpack_rejects_extra_files_and_wrong_content() {
        use zip::write::SimpleFileOptions;
        let (_, manifest) = fixture();
        let component = &manifest.components["worker"];
        let root =
            std::env::temp_dir().join(format!("captionflow-archive-test-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let archive = root.join("component.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
        zip.start_file("../escape", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"new worker").unwrap();
        zip.finish().unwrap();
        assert!(unpack(&archive, &root.join("stage"), component).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn revoked_consent_stops_downloads_before_network_access() {
        let online = AtomicBool::new(false);
        assert!(
            fetch(
                "https://example.org/",
                Path::new("must-not-exist"),
                1,
                &"a".repeat(64),
                &online
            )
            .is_err()
        );
    }
}
