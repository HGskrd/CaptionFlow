fn main() {
    println!("cargo:rerun-if-changed=../portable/resources/CaptionFlow.ico");
    println!("cargo:rerun-if-changed=windows.manifest");
    #[cfg(windows)]
    {
        winresource::WindowsResource::new()
            .set_manifest_file("windows.manifest")
            .set_icon("../portable/resources/CaptionFlow.ico")
            .set("ProductName", "CaptionFlow")
            .set("FileDescription", "CaptionFlow")
            .compile()
            .expect("embed the CaptionFlow icon");
    }
}
