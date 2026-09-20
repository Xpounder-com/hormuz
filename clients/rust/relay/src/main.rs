// Linux compiles this module only for the synthetic Unix pipe tests. The
// executable still requires a supported native secure store below.
#[cfg(any(target_os = "macos", all(test, unix)))]
mod helper_exchange;

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
