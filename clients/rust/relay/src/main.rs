// Linux uses the same bounded Unix helper exchange when a terminal invocation
// explicitly opts in; both platforms retain the Unix pipe fixtures.
#[cfg(any(target_os = "macos", target_os = "linux", all(test, unix)))]
mod helper_exchange;

// The same suspended-start Job Object owner used for launched AI clients also
// owns Windows optimizer helpers. Keep the native calls in one reviewed module.
#[cfg(windows)]
mod helper_exchange_windows;
#[cfg(windows)]
#[path = "process_scope.rs"]
mod process_scope;

#[cfg(any(target_os = "macos", target_os = "linux", windows))]
mod native_cli;

#[cfg(any(target_os = "macos", target_os = "linux", windows))]
fn main() -> std::process::ExitCode {
    native_cli::main()
}

#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
fn main() -> std::process::ExitCode {
    eprintln!("relay error: native_secure_store_unavailable");
    std::process::ExitCode::FAILURE
}
