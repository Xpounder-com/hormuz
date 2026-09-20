//! Pure companion interaction policy for #338. No runtime, windows or credentials.
//!
//! A shell serializes events on its UI executor, batches equal-time observations,
//! and renders only after a batch. User/native observations precede timer callbacks
//! within that batch; otherwise input order is preserved. The shell owns a
//! monotonic clock, timer execution, hit testing, OS focus and accessibility.

#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};

/// Existing Swift hover and fold delay. This is not a polling interval.
pub const DISMISSAL_DELAY_MS: u64 = 250;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Metric {
    Cost,
    Tokens,
    Requests,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum VisibilityMode {
    Always,
    Fold,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Visibility {
    Hidden,
    Folded,
    Expanded,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SettingsPage {
    Home,
    Connection,
    Client,
    Appearance,
    Setup,
    Review,
}

impl SettingsPage {
    fn back(self) -> Option<Self> {
        match self {
            Self::Home => None,
            Self::Review => Some(Self::Client),
            Self::Setup => Some(Self::Connection),
            _ => Some(Self::Home),
        }
    }
}

/// Native hit testing maps visible surfaces to these semantic regions. A late
/// exit from one region cannot erase an already-observed enter into another.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "region", rename_all = "snake_case", deny_unknown_fields)]
pub enum PointerTarget {
    Outside,
    Widget,
    Metric { metric: Metric },
    Details,
    SettingsHandle,
    Settings,
}

/// Observed focus within a whole surface, not an individual child control.
/// Native tab order and screen-reader focus stay in the shell.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FocusTarget {
    Widget,
    Details,
    Settings,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(transparent)]
pub struct TimerToken(u64);

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TimerKind {
    DismissDetails,
    Fold,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub struct Timer {
    pub token: TimerToken,
    pub kind: TimerKind,
    pub deadline_ms: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Event {
    PointerEnter {
        target: PointerTarget,
    },
    PointerExit {
        target: PointerTarget,
    },
    TogglePin {
        metric: Metric,
    },
    /// Explicit menu/keyboard/screen-reader activation can reopen a hidden widget.
    ShowAndPin {
        metric: Metric,
    },
    OpenSettings {
        page: SettingsPage,
    },
    CloseSettings,
    Back,
    DismissDetails,
    OutsideClick,
    Escape,
    /// Explicit native Fold/Expand control. Folding keeps widget-button focus;
    /// a later pointer re-entry or Reopen resumes the ordinary expansion policy.
    ToggleFold,
    Hide,
    /// Repeated application/tray activation keeps an already-open card intact.
    Reopen,
    SetVisibilityMode {
        mode: VisibilityMode,
    },
    FocusChanged {
        target: Option<FocusTarget>,
    },
    TimerFired {
        token: TimerToken,
    },
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Effect {
    ScheduleTimer {
        timer: Timer,
    },
    CancelTimer {
        token: TimerToken,
    },
    /// Intent only: state changes focus only after a native FocusChanged event.
    RequestFocus {
        target: FocusTarget,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InteractionError {
    ClockWentBackwards,
    TimerDeadlineOverflow,
    TimerTokenExhausted,
}

/// Read-only, credential-free, in-memory projection; never persisted or decoded.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Snapshot {
    pub visibility_mode: VisibilityMode,
    pub visibility: Visibility,
    pub selected_metric: Option<Metric>,
    pub pinned_metric: Option<Metric>,
    pub settings_page: Option<SettingsPage>,
    pub pointer: PointerTarget,
    pub keyboard_focus: Option<FocusTarget>,
    pub keep_open_after_outside_click: bool,
    pub pending_timers: Vec<Timer>,
}

#[derive(Clone, Debug)]
pub struct Interaction {
    mode: VisibilityMode,
    visible: bool,
    transient_expanded: bool,
    explicitly_folded: bool,
    selected: Option<Metric>,
    pinned: Option<Metric>,
    // Swift togglePin cancels dismissal when unpinning, including activation
    // while the pointer is outside. A later metric/card leave resumes dismissal.
    unpinned_waiting_for_leave: bool,
    settings: Option<SettingsPage>,
    pointer: PointerTarget,
    focus: Option<FocusTarget>,
    keep_open: bool,
    detail_timer: Option<Timer>,
    fold_timer: Option<Timer>,
    next_token: u64,
    last_time_ms: Option<u64>,
}

impl Interaction {
    pub fn new(mode: VisibilityMode, visible: bool) -> Self {
        Self {
            mode,
            visible,
            transient_expanded: false,
            explicitly_folded: false,
            selected: None,
            pinned: None,
            unpinned_waiting_for_leave: false,
            settings: None,
            pointer: PointerTarget::Outside,
            focus: None,
            keep_open: false,
            detail_timer: None,
            fold_timer: None,
            next_token: 0,
            last_time_ms: None,
        }
    }

    pub fn snapshot(&self) -> Snapshot {
        let visibility = if !self.visible {
            Visibility::Hidden
        } else if self.explicitly_folded {
            Visibility::Folded
        } else if self.mode == VisibilityMode::Always
            || self.transient_expanded
            || self.selected.is_some()
            || self.settings.is_some()
            || self.focus.is_some()
            || self.pointer != PointerTarget::Outside
        {
            Visibility::Expanded
        } else {
            Visibility::Folded
        };
        Snapshot {
            visibility_mode: self.mode,
            visibility,
            selected_metric: self.selected,
            pinned_metric: self.pinned,
            settings_page: self.settings,
            pointer: self.pointer,
            keyboard_focus: self.focus,
            keep_open_after_outside_click: self.keep_open,
            pending_timers: self
                .detail_timer
                .into_iter()
                .chain(self.fold_timer)
                .collect(),
        }
    }

    /// Apply one UI-executor turn atomically. Times must be monotonic. Batch
    /// equal-time observations together: input first (stable order), timers last
    /// (stable order), then render/execute effects in returned order. A batch
    /// error leaves all state, timer IDs and last-time tracking unchanged.
    ///
    /// A timer callback must echo its token, not look up the newest timer. Late,
    /// duplicate, cancelled and early callbacks cannot dismiss newer UI. Early
    /// delivery leaves the timer pending; the adapter must retain its deadline.
    pub fn dispatch_batch(
        &mut self,
        now_ms: u64,
        events: &[Event],
    ) -> Result<Vec<Effect>, InteractionError> {
        if self.last_time_ms.is_some_and(|last| now_ms < last) {
            return Err(InteractionError::ClockWentBackwards);
        }
        let mut next = self.clone();
        let mut effects = Vec::new();
        for is_timer in [false, true] {
            for event in events {
                if matches!(event, Event::TimerFired { .. }) == is_timer {
                    next.apply(now_ms, *event, &mut effects)?;
                }
            }
        }
        // A later Hide/close in the same batch invalidates earlier focus intent.
        // Focus is requested only once, for the last still-visible destination.
        let final_focus = effects.iter().rev().find_map(|effect| match effect {
            Effect::RequestFocus { target }
                if next.visible && next.focus_target_exists(*target) =>
            {
                Some(*target)
            }
            _ => None,
        });
        effects.retain(|effect| !matches!(effect, Effect::RequestFocus { .. }));
        if let Some(target) = final_focus {
            effects.push(Effect::RequestFocus { target });
        }
        next.last_time_ms = Some(now_ms);
        *self = next;
        Ok(effects)
    }

    fn apply(
        &mut self,
        now_ms: u64,
        event: Event,
        effects: &mut Vec<Effect>,
    ) -> Result<(), InteractionError> {
        match event {
            Event::Hide => {
                self.visible = false;
                self.transient_expanded = false;
                self.explicitly_folded = false;
                self.keep_open = false;
                self.settings = None;
                self.clear_details();
                self.pointer = PointerTarget::Outside;
                self.focus = None;
            }
            Event::Reopen => self.reopen(effects),
            Event::SetVisibilityMode { mode } => {
                self.mode = mode;
                self.transient_expanded = false;
                self.explicitly_folded = false;
                self.keep_open = false;
            }
            Event::OpenSettings { page } => {
                self.reopen(effects);
                self.clear_details();
                self.settings = Some(page);
                effects.push(Effect::RequestFocus {
                    target: FocusTarget::Settings,
                });
            }
            Event::ShowAndPin { metric } => {
                self.reopen(effects);
                self.close_settings(effects);
                self.selected = Some(metric);
                self.pinned = Some(metric);
                self.unpinned_waiting_for_leave = false;
            }
            Event::TimerFired { token } => self.fire_timer(now_ms, token),
            _ if !self.visible => return Ok(()),
            Event::ToggleFold => {
                if self.snapshot().visibility == Visibility::Folded {
                    self.mode = VisibilityMode::Always;
                    self.explicitly_folded = false;
                } else {
                    self.mode = VisibilityMode::Fold;
                    self.explicitly_folded = true;
                    self.transient_expanded = false;
                    self.keep_open = false;
                    self.close_settings(effects);
                    self.dismiss_details(effects);
                    // These content surfaces disappear when explicitly folded.
                    // The shell will report the next actual pointer location.
                    if self.pointer != PointerTarget::Widget {
                        self.pointer = PointerTarget::Outside;
                    }
                }
            }
            Event::PointerEnter { target } => {
                if !self.pointer_target_exists(target) {
                    return Ok(());
                }
                self.pointer = target;
                if matches!(
                    target,
                    PointerTarget::Widget
                        | PointerTarget::Metric { .. }
                        | PointerTarget::SettingsHandle
                ) {
                    self.explicitly_folded = false;
                    self.keep_open = false;
                    self.transient_expanded = true;
                }
                if let PointerTarget::Metric { metric } = target {
                    if self.pinned.is_none() && self.settings.is_none() {
                        self.selected = Some(metric);
                        self.unpinned_waiting_for_leave = false;
                    }
                }
            }
            Event::PointerExit { target } => {
                // A leave for the selected metric/card resumes Swift's hover
                // dismissal even if another region's enter arrived first.
                // The current pointer region still independently prevents
                // dismissal while inside the card or a newly hovered metric.
                if target == PointerTarget::Details
                    || matches!(target, PointerTarget::Metric { metric } if self.selected == Some(metric))
                {
                    self.unpinned_waiting_for_leave = false;
                }
                if self.pointer == target {
                    self.pointer = PointerTarget::Outside;
                }
            }
            Event::TogglePin { metric } => {
                self.reopen(effects);
                self.close_settings(effects);
                self.selected = Some(metric);
                self.pinned = (self.pinned != Some(metric)).then_some(metric);
                self.unpinned_waiting_for_leave = self.pinned.is_none();
            }
            Event::CloseSettings => self.close_settings(effects),
            Event::Back => self.back(effects),
            Event::Escape => {
                if self.settings.is_some() {
                    self.back(effects);
                } else {
                    self.dismiss_details(effects);
                }
            }
            Event::DismissDetails => self.dismiss_details(effects),
            Event::OutsideClick => {
                self.pointer = PointerTarget::Outside;
                self.focus = None;
                if self.settings.is_some() || self.selected.is_some() {
                    self.settings = None;
                    self.clear_details();
                    self.pointer = PointerTarget::Outside;
                    self.focus = None;
                    self.keep_open = true;
                    self.transient_expanded = true;
                }
            }
            Event::FocusChanged { target } => {
                if target.is_some_and(|target| !self.focus_target_exists(target)) {
                    return Ok(());
                }
                self.focus = target;
                if target.is_some() {
                    self.transient_expanded = true;
                }
            }
        }
        self.reconcile_timers(now_ms, effects)
    }

    fn reopen(&mut self, effects: &mut Vec<Effect>) {
        if let Some(timer) = self.fold_timer.take() {
            effects.push(Effect::CancelTimer { token: timer.token });
        }
        self.visible = true;
        self.explicitly_folded = false;
        self.transient_expanded = true;
        self.keep_open = false;
    }

    fn pointer_target_exists(&self, target: PointerTarget) -> bool {
        match target {
            PointerTarget::Details => self.selected.is_some(),
            PointerTarget::Settings => self.settings.is_some(),
            _ => true,
        }
    }

    fn focus_target_exists(&self, target: FocusTarget) -> bool {
        match target {
            FocusTarget::Widget => true,
            FocusTarget::Details => self.selected.is_some(),
            FocusTarget::Settings => self.settings.is_some(),
        }
    }

    fn clear_details(&mut self) {
        self.selected = None;
        self.pinned = None;
        self.unpinned_waiting_for_leave = false;
        if self.pointer == PointerTarget::Details {
            self.pointer = PointerTarget::Outside;
        }
        if self.focus == Some(FocusTarget::Details) {
            self.focus = None;
        }
    }

    fn dismiss_details(&mut self, effects: &mut Vec<Effect>) {
        let had_focus = self.focus == Some(FocusTarget::Details);
        self.clear_details();
        if had_focus {
            effects.push(Effect::RequestFocus {
                target: FocusTarget::Widget,
            });
        }
    }

    fn close_settings(&mut self, effects: &mut Vec<Effect>) {
        self.settings = None;
        if self.pointer == PointerTarget::Settings {
            self.pointer = PointerTarget::Outside;
        }
        if self.focus == Some(FocusTarget::Settings) {
            self.focus = None;
            effects.push(Effect::RequestFocus {
                target: FocusTarget::Widget,
            });
        }
    }

    fn back(&mut self, effects: &mut Vec<Effect>) {
        if let Some(page) = self.settings {
            self.settings = page.back();
            if self.settings.is_none() {
                self.close_settings(effects);
            }
        }
    }

    fn fire_timer(&mut self, now_ms: u64, token: TimerToken) {
        if self
            .detail_timer
            .is_some_and(|timer| timer.token == token && now_ms >= timer.deadline_ms)
        {
            self.detail_timer = None;
            self.clear_details();
        } else if self
            .fold_timer
            .is_some_and(|timer| timer.token == token && now_ms >= timer.deadline_ms)
        {
            self.fold_timer = None;
            self.transient_expanded = false;
        }
    }

    fn reconcile_timers(
        &mut self,
        now_ms: u64,
        effects: &mut Vec<Effect>,
    ) -> Result<(), InteractionError> {
        let dismiss_details = self.visible
            && self.selected.is_some()
            && self.pinned.is_none()
            && !self.unpinned_waiting_for_leave
            && self.pointer != PointerTarget::Details
            && !matches!(self.pointer, PointerTarget::Metric { .. })
            && self.focus != Some(FocusTarget::Details);
        let fold = self.visible
            && self.mode == VisibilityMode::Fold
            && !self.explicitly_folded
            && self.transient_expanded
            && !self.keep_open
            && self.selected.is_none()
            && self.settings.is_none()
            && self.pointer == PointerTarget::Outside
            && self.focus.is_none();
        self.update_timer(TimerKind::DismissDetails, dismiss_details, now_ms, effects)?;
        self.update_timer(TimerKind::Fold, fold, now_ms, effects)
    }

    fn update_timer(
        &mut self,
        kind: TimerKind,
        needed: bool,
        now_ms: u64,
        effects: &mut Vec<Effect>,
    ) -> Result<(), InteractionError> {
        let slot = match kind {
            TimerKind::DismissDetails => &mut self.detail_timer,
            TimerKind::Fold => &mut self.fold_timer,
        };
        match (needed, *slot) {
            (false, Some(timer)) => {
                *slot = None;
                effects.push(Effect::CancelTimer { token: timer.token });
            }
            (true, None) => {
                let deadline_ms = now_ms
                    .checked_add(DISMISSAL_DELAY_MS)
                    .ok_or(InteractionError::TimerDeadlineOverflow)?;
                self.next_token = self
                    .next_token
                    .checked_add(1)
                    .ok_or(InteractionError::TimerTokenExhausted)?;
                let timer = Timer {
                    token: TimerToken(self.next_token),
                    kind,
                    deadline_ms,
                };
                *slot = Some(timer);
                effects.push(Effect::ScheduleTimer { timer });
            }
            _ => {}
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
