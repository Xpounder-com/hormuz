#[cfg(any(target_os = "macos", windows))]
mod native_cli;

#[cfg(any(target_os = "macos", windows))]
fn main() -> std::process::ExitCode {
    native_cli::main()
}

#[cfg(not(any(target_os = "macos", windows)))]
fn main() -> std::process::ExitCode {
    eprintln!("relay error: native_secure_store_unavailable");
    std::process::ExitCode::FAILURE
}
