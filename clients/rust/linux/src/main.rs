#[cfg(all(target_os = "linux", feature = "gtk-ui"))]
fn main() -> gtk::glib::ExitCode {
    hormuz_linux::gtk_shell::run()
}

#[cfg(not(all(target_os = "linux", feature = "gtk-ui")))]
fn main() {
    eprintln!("The Linux GTK companion requires Linux and the gtk-ui build feature.");
    std::process::exit(2);
}
