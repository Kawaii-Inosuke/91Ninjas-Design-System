//! Windows only: embed the app icon and version info (VERSIONINFO) into `vectorcraft.exe`.
//!
//! On every other target this does nothing. A missing resource compiler is a warning, so a
//! cross-compile from macOS or Linux still links, unless `VECTORCRAFT_REQUIRE_WINRES=1` turns it into
//! an error (for release builds).

fn main() -> Result<(), String> {
    println!("cargo:rerun-if-changed=build.rs");
    // 91studio: the 91Ninjas Studio icon and names in the .exe (D-040).
    println!("cargo:rerun-if-changed=../../assets/91studio/icon.ico");
    println!("cargo:rerun-if-env-changed=VECTORCRAFT_REQUIRE_WINRES");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return Ok(());
    }
    let mut res = winresource::WindowsResource::new();
    res.set_icon("../../assets/91studio/icon.ico")
        .set("ProductName", "91Ninjas Studio")
        .set("FileDescription", "91Ninjas Studio: on-brand design with Claude")
        .set("CompanyName", "91Ninjas")
        .set("LegalCopyright", "© 2026 91Ninjas. All rights reserved. Internal use only.")
        .set("OriginalFilename", "91ninjas-studio.exe")
        .set("InternalName", "91ninjas-studio");
    if let Err(e) = res.compile() {
        if std::env::var_os("VECTORCRAFT_REQUIRE_WINRES").is_some() {
            return Err(format!("embedding Windows resources failed: {e}"));
        }
        println!("cargo:warning=vectorcraft.exe built without icon/version resources: {e}");
    }
    Ok(())
}
