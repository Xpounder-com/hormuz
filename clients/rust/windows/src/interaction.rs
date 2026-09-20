//! Bounded GUI-turn queue and one-shot timer identities, without native handles.
use hormuz_client_interaction::{
    Effect, Event, FocusTarget, Interaction, InteractionError, Snapshot, Timer, VisibilityMode,
};

const MAX_EVENTS: usize = 128;
const FIRST_TIMER_ID: usize = 1024; // Separate from the shell's smoke timer.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    QueueFull,
    TimerIdsExhausted,
    Reducer(InteractionError),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimerAction {
    Arm { id: usize, deadline_ms: u64 },
    Cancel { id: usize },
}

#[derive(Clone, Copy, Debug)]
struct NativeTimer {
    id: usize,
    timer: Timer,
    armed: bool,
}

pub struct Update {
    pub snapshot: Snapshot,
    pub timers: Vec<TimerAction>,
    pub focus: Option<FocusTarget>,
}

#[derive(Clone)]
pub struct Bridge {
    state: Interaction,
    events: Vec<Event>,
    timers: Vec<NativeTimer>,
    next_id: usize,
}

impl Bridge {
    pub fn new() -> Self {
        Self {
            state: Interaction::new(VisibilityMode::Always, true),
            events: Vec::new(),
            timers: Vec::new(),
            next_id: FIRST_TIMER_ID,
        }
    }

    pub fn push(&mut self, event: Event) -> Result<(), Error> {
        if self.events.len() == MAX_EVENTS {
            return Err(Error::QueueFull);
        }
        self.events.push(event);
        Ok(())
    }

    /// Capture the token belonging to this native ID now, never the newest slot.
    /// The caller kills this repeating Win32 timer before posting the drain.
    pub fn timer_fired(&mut self, id: usize) -> Result<bool, Error> {
        let Some(index) = self
            .timers
            .iter()
            .position(|timer| timer.id == id && timer.armed)
        else {
            return Ok(false);
        };
        self.push(Event::TimerFired {
            token: self.timers[index].timer.token,
        })?;
        self.timers[index].armed = false;
        Ok(true)
    }

    /// All observations collected before the posted drain form one UI turn.
    /// Shared policy orders input before callbacks and publishes one snapshot.
    pub fn flush(&mut self, now_ms: u64) -> Result<Update, Error> {
        let mut next = self.clone();
        let effects = next
            .state
            .dispatch_batch(now_ms, &next.events)
            .map_err(Error::Reducer)?;
        let snapshot = next.state.snapshot();
        let mut actions = Vec::new();
        next.timers.retain(|native| {
            let keep = snapshot.pending_timers.contains(&native.timer);
            if !keep {
                actions.push(TimerAction::Cancel { id: native.id });
            }
            keep
        });
        // Reconcile only the final timers: transient schedule/cancel pairs in
        // the same batch never allocate a native timer or steal native focus.
        for timer in &snapshot.pending_timers {
            let native = if let Some(index) = next.timers.iter().position(|n| n.timer == *timer) {
                &mut next.timers[index]
            } else {
                let id = next.next_id;
                next.next_id = id.checked_add(1).ok_or(Error::TimerIdsExhausted)?;
                next.timers.push(NativeTimer {
                    id,
                    timer: *timer,
                    armed: false,
                });
                next.timers.last_mut().unwrap()
            };
            if !native.armed {
                actions.push(TimerAction::Arm {
                    id: native.id,
                    deadline_ms: timer.deadline_ms,
                });
                native.armed = true;
            }
        }
        next.events.clear();
        let focus = effects.into_iter().find_map(|effect| match effect {
            Effect::RequestFocus { target } => Some(target),
            _ => None,
        });
        *self = next;
        Ok(Update {
            snapshot,
            timers: actions,
            focus,
        })
    }

    pub fn stop(&mut self) -> Vec<usize> {
        self.events.clear();
        self.timers.drain(..).map(|timer| timer.id).collect()
    }
}

#[cfg(test)]
#[path = "interaction_tests.rs"]
mod tests;
