#ifndef HORMUZ_RUST_H
#define HORMUZ_RUST_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// ABI v1: opaque allocations are owned by the function returning them. Free
// exactly once with the matching function. Never race a call with its free.
// Secret handles are privileged custody transfers, not display allocations.
typedef struct HormuzUi HormuzUi;
typedef struct HormuzBytes HormuzBytes;
typedef struct HormuzSecret HormuzSecret;
typedef struct HormuzSubscription HormuzSubscription;

typedef struct {
    void *context;
    // read: 0 absent, 1 transfers one owned secret, -1 unavailable.
    int32_t (*read)(void *, HormuzSecret **);
    // write: the opaque secret is borrowed only for this callback; do not free.
    int32_t (*write)(void *, const HormuzSecret *);
    int32_t (*delete_record)(void *);
    int32_t (*open_browser)(void *, const uint8_t *, size_t);
} HormuzHost;

uint32_t hormuz_ui_abi_version(void);
// Call from a bounded worker. Context/callbacks must be thread-safe and live
// through free, which cancels and joins the worker before returning. Custody
// remains in the host executable; no implicit Rust OS credential adapter.
HormuzUi *hormuz_ui_new(const uint8_t *, size_t, HormuzHost);
void hormuz_ui_free(HormuzUi *);
bool hormuz_ui_connect(const HormuzUi *, const uint8_t *, size_t);
void hormuz_ui_disconnect(const HormuzUi *);
bool hormuz_ui_retry(const HormuzUi *);
bool hormuz_ui_cancel(const HormuzUi *); // never cancels accepted sign-out
bool hormuz_ui_refresh(const HormuzUi *); // coalesced; honors visibility/power gates
bool hormuz_ui_visibility(const HormuzUi *, uint32_t); // hidden=0 summary=1 detail=2
bool hormuz_ui_lifecycle(const HormuzUi *, uint32_t); // sleep,wake,network,lock,unlock,quit
HormuzBytes *hormuz_ui_snapshot(const HormuzUi *);

// One subscription. Wake ONLY enqueues a UI turn: no reentry or blocking.
// Drain take_change on that turn. A null change means no actual state change.
// Unsubscribe synchronously drains in-progress wakes. Subscription may outlive
// Ui; it then produces no wakes and remains safe to free.
HormuzSubscription *hormuz_ui_subscribe(const HormuzUi *, void (*wake)(void *), void *);
void hormuz_ui_unsubscribe(HormuzSubscription *);
HormuzBytes *hormuz_ui_take_change(const HormuzUi *);

// Launch execution and settings persistence remain shell-owned. The wrapper
// routes these validated inputs through the existing governed launcher/settings
// adapters; no arbitrary executable, credential or raw request enters the API.
HormuzBytes *hormuz_ui_launch_profile(const HormuzUi *);
HormuzBytes *hormuz_ui_context_setting(bool);
size_t hormuz_ui_bytes_length(const HormuzBytes *);
bool hormuz_ui_bytes_copy(const HormuzBytes *, uint8_t *, size_t);
void hormuz_ui_bytes_free(HormuzBytes *);

// Explicit privileged custody API. No borrowed secret-byte pointer is exposed.
// The caller owns its copies and must erase them when possible; Rust zeroizes
// its owned secret allocation. Secret and display frees are not interchangeable.
HormuzSecret *hormuz_ui_secret_new(const uint8_t *, size_t);
size_t hormuz_ui_secret_length(const HormuzSecret *);
bool hormuz_ui_secret_copy(const HormuzSecret *, uint8_t *, size_t);
void hormuz_ui_secret_free(HormuzSecret *);
#endif
