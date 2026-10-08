//! Linux presentation policy; authentication remains in the shared worker.
#![deny(unsafe_op_in_unsafe_fn)]

pub use hormuz_client_desktop::{connection, presentation};

use hormuz_client_interaction::{
    Effect, Event, Interaction, InteractionError, Snapshot, VisibilityMode,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DisplayBackend {
    Wayland,
    X11,
    Other,
}

/// These are observed shell capabilities, not a Linux support declaration.
/// This shell deliberately uses an ordinary window on every compositor. It
/// never hides behind a missing layer-shell or an unavailable tray extension.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capabilities {
    pub display: DisplayBackend,
    pub layer_shell: bool,
    pub tray: bool,
}

impl Capabilities {
    pub fn ordinary_window(display: DisplayBackend) -> Self {
        Self {
            display,
            layer_shell: false,
            tray: false,
        }
    }

    pub fn description(self) -> &'static str {
        match self.display {
            DisplayBackend::Wayland => "Wayland · ordinary window · no edge overlay or tray",
            DisplayBackend::X11 => "X11 · ordinary window · no edge overlay or tray",
            DisplayBackend::Other => "Ordinary window · no edge overlay or tray",
        }
    }
}

/// Only interaction rendering lives here; this is not another session model.
pub struct Controls(Interaction);
impl Default for Controls {
    fn default() -> Self {
        Self(Interaction::new(VisibilityMode::Always, true))
    }
}
impl Controls {
    pub fn snapshot(&self) -> Snapshot {
        self.0.snapshot()
    }
    pub fn dispatch(&mut self, now_ms: u64, event: Event) -> Result<Vec<Effect>, InteractionError> {
        self.0.dispatch_batch(now_ms, &[event])
    }
}

#[cfg(all(target_os = "linux", feature = "gtk-ui"))]
pub mod gtk_shell;

#[cfg(test)]
mod tests {
    use super::*;
    use hormuz_client_interaction::{Metric, Visibility};

    #[test]
    fn every_backend_keeps_an_ordinary_recoverable_window() {
        for display in [
            DisplayBackend::Wayland,
            DisplayBackend::X11,
            DisplayBackend::Other,
        ] {
            let capabilities = Capabilities::ordinary_window(display);
            assert!(!capabilities.layer_shell);
            assert!(!capabilities.tray);
            assert!(capabilities.description().contains("window"));
            let mut controls = Controls::default();
            assert_eq!(controls.snapshot().visibility, Visibility::Expanded);
            controls.dispatch(0, Event::ToggleFold).unwrap();
            assert_eq!(controls.snapshot().visibility, Visibility::Folded);
            controls.dispatch(1, Event::Reopen).unwrap();
            assert_eq!(controls.snapshot().visibility, Visibility::Expanded);
        }
    }

    #[test]
    fn keyboard_details_use_the_shared_pin_and_escape_contract() {
        let mut controls = Controls::default();
        controls
            .dispatch(
                0,
                Event::TogglePin {
                    metric: Metric::Tokens,
                },
            )
            .unwrap();
        assert_eq!(controls.snapshot().pinned_metric, Some(Metric::Tokens));
        controls.dispatch(1, Event::Escape).unwrap();
        assert!(controls.snapshot().selected_metric.is_none());
        assert_ne!(controls.snapshot().visibility, Visibility::Hidden);
    }
}
