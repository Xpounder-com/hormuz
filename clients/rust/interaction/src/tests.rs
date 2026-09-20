use super::*;

fn dispatch(state: &mut Interaction, now: u64, events: &[Event]) -> Vec<Effect> {
    state.dispatch_batch(now, events).unwrap()
}

fn enter(target: PointerTarget) -> Event {
    Event::PointerEnter { target }
}

fn exit(target: PointerTarget) -> Event {
    Event::PointerExit { target }
}

fn fire(timer: Timer) -> Event {
    Event::TimerFired { token: timer.token }
}

#[test]
fn external_input_at_deadline_wins_regardless_of_callback_position_in_batch() {
    for timer_first in [false, true] {
        let mut state = Interaction::new(VisibilityMode::Fold, true);
        let metric = PointerTarget::Metric {
            metric: Metric::Cost,
        };
        dispatch(&mut state, 0, &[enter(metric)]);
        dispatch(&mut state, 10, &[exit(metric)]);
        let timer = state.detail_timer.unwrap();
        let mut events = [enter(PointerTarget::Details), fire(timer)];
        if timer_first {
            events.reverse();
        }
        let effects = dispatch(&mut state, 260, &events);
        assert_eq!(state.selected, Some(Metric::Cost));
        assert_eq!(state.pointer, PointerTarget::Details);
        assert_eq!(effects, [Effect::CancelTimer { token: timer.token }]);
        dispatch(&mut state, 500, &[fire(timer), fire(timer)]);
        assert_eq!(state.selected, Some(Metric::Cost));
    }
}

#[test]
fn reopening_at_fold_deadline_replaces_timer_instead_of_refolding() {
    let mut state = Interaction::new(VisibilityMode::Fold, false);
    dispatch(&mut state, 0, &[Event::Reopen]);
    let old = state.fold_timer.unwrap();
    dispatch(&mut state, 250, &[fire(old), Event::Reopen]);
    let current = state.fold_timer.unwrap();
    assert_ne!(old.token, current.token);
    assert_eq!(current.deadline_ms, 500);
    assert_eq!(state.snapshot().visibility, Visibility::Expanded);
    dispatch(&mut state, 499, &[fire(current)]);
    assert_eq!(state.fold_timer, Some(current));
    dispatch(&mut state, 500, &[fire(current)]);
    assert_eq!(state.snapshot().visibility, Visibility::Folded);
}

#[test]
fn focus_changes_between_settings_controls_never_schedule_a_fold() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    dispatch(
        &mut state,
        0,
        &[Event::OpenSettings {
            page: SettingsPage::Setup,
        }],
    );
    for now in 1..20 {
        let effects = dispatch(
            &mut state,
            now,
            &[
                Event::FocusChanged { target: None },
                Event::FocusChanged {
                    target: Some(FocusTarget::Settings),
                },
            ],
        );
        assert!(effects.is_empty());
        assert!(state.fold_timer.is_none());
        assert_eq!(state.settings, Some(SettingsPage::Setup));
    }
    let effects = dispatch(&mut state, 20, &[Event::Escape]);
    assert!(effects.is_empty());
    assert_eq!(state.settings, Some(SettingsPage::Connection));
    assert_eq!(state.focus, Some(FocusTarget::Settings));
}

#[test]
fn stale_pointer_exit_cannot_overwrite_newer_pointer_region() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    let cost = PointerTarget::Metric {
        metric: Metric::Cost,
    };
    let tokens = PointerTarget::Metric {
        metric: Metric::Tokens,
    };
    dispatch(&mut state, 0, &[enter(cost), enter(tokens), exit(cost)]);
    assert_eq!(state.selected, Some(Metric::Tokens));
    assert_eq!(state.pointer, tokens);
    assert!(state.detail_timer.is_none());
    dispatch(
        &mut state,
        1,
        &[enter(PointerTarget::Details), exit(tokens)],
    );
    assert_eq!(state.pointer, PointerTarget::Details);
    assert!(state.detail_timer.is_none());
}

#[test]
fn focus_request_is_not_a_focus_observation_and_hide_invalidates_pending_requests() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    let effects = dispatch(
        &mut state,
        0,
        &[Event::OpenSettings {
            page: SettingsPage::Home,
        }],
    );
    assert_eq!(
        effects,
        [Effect::RequestFocus {
            target: FocusTarget::Settings
        }]
    );
    assert_eq!(state.focus, None);
    let effects = dispatch(
        &mut state,
        1,
        &[
            Event::OpenSettings {
                page: SettingsPage::Connection,
            },
            Event::Hide,
            Event::FocusChanged {
                target: Some(FocusTarget::Settings),
            },
            enter(PointerTarget::Widget),
        ],
    );
    assert!(effects.is_empty());
    assert_eq!(state.snapshot().visibility, Visibility::Hidden);
    assert_eq!(state.focus, None);
    assert_eq!(state.pointer, PointerTarget::Outside);
    assert_eq!(state.settings, None);
    dispatch(&mut state, 2, &[Event::Reopen]);
    dispatch(
        &mut state,
        3,
        &[
            Event::FocusChanged {
                target: Some(FocusTarget::Settings),
            },
            enter(PointerTarget::Details),
        ],
    );
    assert_eq!(state.focus, None);
    assert_eq!(state.pointer, PointerTarget::Outside);
}

#[test]
fn failed_batch_does_not_advance_clock_mutate_state_or_consume_tokens() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    dispatch(&mut state, 20, &[enter(PointerTarget::Widget)]);
    let before = state.snapshot();
    assert_eq!(
        state.dispatch_batch(19, &[Event::Hide]),
        Err(InteractionError::ClockWentBackwards)
    );
    assert_eq!(state.snapshot(), before);
    assert_eq!(
        state.dispatch_batch(u64::MAX, &[Event::Hide, Event::Reopen]),
        Err(InteractionError::TimerDeadlineOverflow)
    );
    assert_eq!(state.snapshot(), before);
    assert_eq!(state.last_time_ms, Some(20));
    assert_eq!(state.next_token, 0);
    state.next_token = u64::MAX;
    assert_eq!(
        state.dispatch_batch(21, &[exit(PointerTarget::Widget)]),
        Err(InteractionError::TimerTokenExhausted)
    );
    assert_eq!(state.snapshot(), before);
    assert_eq!(state.last_time_ms, Some(20));
    assert_eq!(state.next_token, u64::MAX);
}

#[test]
fn unrelated_events_do_not_postpone_pending_dismissal() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    let target = PointerTarget::Metric {
        metric: Metric::Requests,
    };
    dispatch(&mut state, 0, &[enter(target)]);
    dispatch(&mut state, 1, &[exit(target)]);
    let timer = state.detail_timer.unwrap();
    dispatch(
        &mut state,
        100,
        &[Event::FocusChanged { target: None }, exit(target)],
    );
    assert_eq!(state.detail_timer, Some(timer));
    dispatch(&mut state, 251, &[fire(timer)]);
    assert_eq!(state.selected, None);
    assert_eq!(state.fold_timer.unwrap().deadline_ms, 501);
}

#[test]
fn reopening_preserves_existing_unpinned_card_and_its_current_dismissal() {
    let mut state = Interaction::new(VisibilityMode::Fold, true);
    let target = PointerTarget::Metric {
        metric: Metric::Cost,
    };
    dispatch(&mut state, 0, &[enter(target)]);
    dispatch(&mut state, 1, &[exit(target)]);
    let timer = state.detail_timer.unwrap();
    // Swift showWidget calls ensureWidgetVisible without cancelling hover work.
    assert!(dispatch(&mut state, 100, &[Event::Reopen]).is_empty());
    assert_eq!(state.selected, Some(Metric::Cost));
    assert_eq!(state.detail_timer, Some(timer));
    dispatch(&mut state, 251, &[fire(timer)]);
    assert_eq!(state.selected, None);
    assert_eq!(state.snapshot().visibility, Visibility::Expanded);
    assert_eq!(state.fold_timer.unwrap().deadline_ms, 501);
}

#[test]
fn replacing_settings_with_pinned_details_discards_unfulfilled_settings_focus_request() {
    let mut state = Interaction::new(VisibilityMode::Fold, false);
    let effects = dispatch(
        &mut state,
        0,
        &[
            Event::OpenSettings {
                page: SettingsPage::Home,
            },
            Event::ShowAndPin {
                metric: Metric::Requests,
            },
        ],
    );
    assert!(effects.is_empty());
    assert_eq!(state.settings, None);
    assert_eq!(state.pinned, Some(Metric::Requests));
    assert_eq!(state.focus, None);
    let effects = dispatch(
        &mut state,
        1,
        &[
            Event::OpenSettings {
                page: SettingsPage::Home,
            },
            Event::OpenSettings {
                page: SettingsPage::Appearance,
            },
        ],
    );
    assert_eq!(
        effects,
        [Effect::RequestFocus {
            target: FocusTarget::Settings
        }]
    );
    assert_eq!(state.settings, Some(SettingsPage::Appearance));
    assert_eq!(state.focus, None);
}

#[test]
fn short_adversarial_traces_keep_ui_and_timer_invariants() {
    let events = [
        enter(PointerTarget::Widget),
        exit(PointerTarget::Widget),
        enter(PointerTarget::Metric {
            metric: Metric::Cost,
        }),
        enter(PointerTarget::Metric {
            metric: Metric::Tokens,
        }),
        enter(PointerTarget::Details),
        exit(PointerTarget::Details),
        enter(PointerTarget::SettingsHandle),
        exit(PointerTarget::SettingsHandle),
        Event::TogglePin {
            metric: Metric::Tokens,
        },
        Event::ShowAndPin {
            metric: Metric::Requests,
        },
        Event::OpenSettings {
            page: SettingsPage::Review,
        },
        Event::CloseSettings,
        Event::Back,
        Event::DismissDetails,
        Event::OutsideClick,
        Event::Escape,
        Event::Hide,
        Event::Reopen,
        Event::FocusChanged {
            target: Some(FocusTarget::Details),
        },
        Event::FocusChanged {
            target: Some(FocusTarget::Settings),
        },
        Event::FocusChanged {
            target: Some(FocusTarget::Widget),
        },
        Event::FocusChanged { target: None },
        Event::SetVisibilityMode {
            mode: VisibilityMode::Always,
        },
        Event::SetVisibilityMode {
            mode: VisibilityMode::Fold,
        },
    ];
    for a in events {
        for b in events {
            for c in events {
                let mut state = Interaction::new(VisibilityMode::Fold, true);
                for (index, event) in [a, b, c].into_iter().enumerate() {
                    let mut batch = vec![event];
                    batch.extend(state.snapshot().pending_timers.into_iter().map(fire));
                    dispatch(&mut state, index as u64 * 300, &batch);
                    assert!(state.pinned.is_none() || state.pinned == state.selected);
                    assert!(state.settings.is_none() || state.selected.is_none());
                    assert!(state
                        .focus
                        .is_none_or(|focus| state.focus_target_exists(focus)));
                    assert!(state.pointer_target_exists(state.pointer));
                    if !state.visible {
                        assert_eq!(state.pointer, PointerTarget::Outside);
                        assert!(state.focus.is_none());
                        assert!(state.selected.is_none());
                        assert!(state.settings.is_none());
                        assert!(state.detail_timer.is_none() && state.fold_timer.is_none());
                    }
                    if state.pinned.is_some() || state.focus == Some(FocusTarget::Details) {
                        assert!(state.detail_timer.is_none());
                    }
                    if state.settings.is_some() || state.focus.is_some() {
                        assert!(state.fold_timer.is_none());
                    }
                    assert!(state.detail_timer.is_none() || state.fold_timer.is_none());
                }
            }
        }
    }
}
