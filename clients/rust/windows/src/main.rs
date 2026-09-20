#![cfg_attr(windows, windows_subsystem = "windows")]
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(any(windows, test))]
mod connection;
#[cfg(any(windows, test))]
mod interaction;
#[cfg(windows)]
mod native;
#[cfg(windows)]
mod network;
#[cfg(any(windows, test))]
mod options;
#[cfg(any(windows, test))]
mod placement;
#[cfg(any(windows, test))]
mod presentation;
#[cfg(windows)]
mod startup;

#[cfg(windows)]
fn main() {
    let Ok(options) = options::Options::parse(std::env::args_os().skip(1).collect()) else {
        std::process::exit(2);
    };
    let code = match startup::begin(&options) {
        Ok(startup::Startup::Reopened) => 0,
        Ok(startup::Startup::Primary { directory, owner }) => {
            native::run(options, directory, owner)
        }
        Err(_) => 1,
    };
    std::process::exit(code);
}

#[cfg(not(windows))]
fn main() {
    eprintln!("The native Windows shell runs on Windows only.");
    std::process::exit(2);
}
