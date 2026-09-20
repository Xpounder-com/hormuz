#![cfg_attr(windows, windows_subsystem = "windows")]
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(windows)]
mod native;
#[cfg(any(windows, test))]
mod placement;

#[cfg(windows)]
fn main() {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let smoke = arguments.len() == 1 && arguments[0] == "--smoke-test";
    if !arguments.is_empty() && !smoke {
        std::process::exit(2);
    }
    std::process::exit(native::run(smoke));
}

#[cfg(not(windows))]
fn main() {
    eprintln!("The native Windows shell runs on Windows only.");
    std::process::exit(2);
}
