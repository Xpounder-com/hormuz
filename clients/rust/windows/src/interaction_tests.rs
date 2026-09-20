use super::*;
use hormuz_client_interaction::{FocusTarget, PointerTarget, Visibility};

fn fold_timer() -> (Bridge, usize) {
    let mut bridge = Bridge::new();
    bridge.push(Event::ToggleFold).unwrap();
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(0).unwrap();
    let [TimerAction::Arm {
        id,
        deadline_ms: 250,
    }] = update.timers.as_slice()
    else {
        panic!("expected one fold deadline")
    };
    (bridge, *id)
}

#[test]
fn queued_native_callback_cannot_override_reopen_and_ids_are_never_reused() {
    let (mut bridge, old) = fold_timer();
    assert!(bridge.timer_fired(old).unwrap());
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(250).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Expanded);
    assert_eq!(update.snapshot.pending_timers[0].deadline_ms, 500);
    assert_eq!(update.timers[0], TimerAction::Cancel { id: old });
    let TimerAction::Arm {
        id: current,
        deadline_ms: 500,
    } = update.timers[1]
    else {
        panic!()
    };
    assert_ne!(old, current);
    assert!(!bridge.timer_fired(old).unwrap());
    assert!(bridge.timer_fired(current).unwrap());
    assert!(!bridge.timer_fired(current).unwrap());
    assert_eq!(
        bridge.flush(500).unwrap().snapshot.visibility,
        Visibility::Folded
    );
}

#[test]
fn early_native_callback_rearms_the_original_id_token_and_deadline() {
    let (mut bridge, id) = fold_timer();
    let before = bridge.state.snapshot().pending_timers[0];
    assert!(bridge.timer_fired(id).unwrap());
    let update = bridge.flush(10).unwrap();
    assert_eq!(
        update.timers,
        [TimerAction::Arm {
            id,
            deadline_ms: 250
        }]
    );
    assert_eq!(update.snapshot.pending_timers, [before]);
    assert!(bridge.timer_fired(id).unwrap());
    assert_eq!(
        bridge.flush(250).unwrap().snapshot.visibility,
        Visibility::Folded
    );
}

#[test]
fn hide_invalidates_delivered_and_undelivered_callbacks_before_a_later_reopen() {
    let (mut bridge, old) = fold_timer();
    bridge.timer_fired(old).unwrap();
    bridge.push(Event::Hide).unwrap();
    let update = bridge.flush(250).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Hidden);
    assert_eq!(update.timers, [TimerAction::Cancel { id: old }]);
    assert!(bridge.stop().is_empty());
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(251).unwrap();
    assert!(matches!(update.timers[0], TimerAction::Arm { id, .. } if id != old));
    assert!(!bridge.timer_fired(old).unwrap());
}

#[test]
fn native_focus_at_deadline_cancels_folding_and_transient_focus_intent_is_not_applied() {
    let (mut bridge, id) = fold_timer();
    bridge.timer_fired(id).unwrap();
    bridge
        .push(Event::FocusChanged {
            target: Some(FocusTarget::Widget),
        })
        .unwrap();
    let update = bridge.flush(250).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Expanded);
    assert!(update.snapshot.pending_timers.is_empty());
    assert_eq!(update.timers, [TimerAction::Cancel { id }]);
    bridge
        .push(Event::OpenSettings {
            page: hormuz_client_interaction::SettingsPage::Home,
        })
        .unwrap();
    bridge.push(Event::Hide).unwrap();
    let update = bridge.flush(251).unwrap();
    assert_eq!(update.focus, None);
    assert!(update.timers.is_empty());
}

#[test]
fn queue_and_timer_identity_exhaustion_are_bounded_and_atomic() {
    let mut bridge = Bridge::new();
    for _ in 0..MAX_EVENTS {
        bridge.push(Event::Escape).unwrap();
    }
    assert_eq!(bridge.push(Event::Hide), Err(Error::QueueFull));
    assert_eq!(bridge.events.len(), MAX_EVENTS);
    bridge.flush(0).unwrap();
    bridge.next_id = usize::MAX;
    bridge.push(Event::ToggleFold).unwrap();
    bridge.push(Event::Reopen).unwrap();
    let before = bridge.state.snapshot();
    assert!(matches!(bridge.flush(1), Err(Error::TimerIdsExhausted)));
    assert_eq!(bridge.state.snapshot(), before);
    assert_eq!(bridge.events.len(), 2);
    assert!(bridge.timers.is_empty());
    bridge.next_id = FIRST_TIMER_ID;
    assert!(
        bridge.flush(0).is_ok(),
        "failed batch must not advance the clock"
    );
}

#[test]
fn explicit_native_fold_retains_focus_and_pointer_until_reentry_or_reopen() {
    let mut bridge = Bridge::new();
    bridge
        .push(Event::FocusChanged {
            target: Some(FocusTarget::Widget),
        })
        .unwrap();
    bridge
        .push(Event::PointerEnter {
            target: PointerTarget::Widget,
        })
        .unwrap();
    bridge.push(Event::ToggleFold).unwrap();
    let update = bridge.flush(0).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Folded);
    assert_eq!(update.snapshot.keyboard_focus, Some(FocusTarget::Widget));
    assert_eq!(update.snapshot.pointer, PointerTarget::Widget);
    assert!(update.timers.is_empty());
    bridge
        .push(Event::PointerExit {
            target: PointerTarget::Widget,
        })
        .unwrap();
    bridge
        .push(Event::PointerEnter {
            target: PointerTarget::Widget,
        })
        .unwrap();
    assert_eq!(
        bridge.flush(1).unwrap().snapshot.visibility,
        Visibility::Expanded
    );
    bridge.push(Event::ToggleFold).unwrap();
    bridge.push(Event::ToggleFold).unwrap();
    assert_eq!(
        bridge.flush(2).unwrap().snapshot.visibility,
        Visibility::Expanded
    );
    bridge.push(Event::ToggleFold).unwrap();
    bridge.push(Event::Hide).unwrap();
    bridge.push(Event::Reopen).unwrap();
    let update = bridge.flush(3).unwrap();
    assert_eq!(update.snapshot.visibility, Visibility::Expanded);
    assert_eq!(update.snapshot.keyboard_focus, None);
}

#[test]
fn every_shared_trace_keeps_its_snapshot_through_the_native_queue_and_timer_bridge() {
    let corpus: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native_client/v1/interactions.json"
    ))
    .unwrap();
    let cases = corpus["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 16);
    let mut steps = 0;
    for case in cases {
        let mut bridge = Bridge::new();
        bridge.state = Interaction::new(
            serde_json::from_value(case["mode"].clone()).unwrap(),
            case["visible"].as_bool().unwrap(),
        );
        for (index, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            for event in step["events"].as_array().unwrap() {
                bridge
                    .push(serde_json::from_value(event.clone()).unwrap())
                    .unwrap();
            }
            let update = bridge.flush(step["at_ms"].as_u64().unwrap()).unwrap();
            assert_eq!(
                serde_json::to_value(update.snapshot).unwrap(),
                step["expected"],
                "{} step {index}",
                case["id"]
            );
            assert!(bridge.timers.len() <= 2);
            assert!(update.timers.len() <= 4);
            steps += 1;
        }
    }
    assert_eq!(steps, 130);
}
