use hormuz_client_core::{ClientError, ConnectionProfile, MAX_RESPONSE_BYTES};
use hormuz_client_transport::{
    Cancellation, ErrorKind, GatewayTransport, RequestOutcome, TransportError,
};
use serde::de::DeserializeOwned;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use zeroize::Zeroizing;

#[derive(Default)]
struct Control {
    cancelled: bool,
    request: Option<Cancellation>,
}

/// One operation and any number of cancellation handles. Do not reuse a handle
/// for concurrent operations. Cancellation is sticky and never undoes egress.
#[derive(Clone, Default)]
pub struct Operation(Arc<Mutex<Control>>);
impl Operation {
    pub fn cancel(&self) {
        let mut control = self.0.lock().unwrap_or_else(|e| e.into_inner());
        control.cancelled = true;
        if let Some(request) = &control.request {
            request.cancel();
        }
    }
    pub fn is_cancelled(&self) -> bool {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).cancelled
    }
    pub(crate) fn check(&self) -> Result<(), ClientError> {
        if self.is_cancelled() {
            Err(ClientError::GatewayUnavailable)
        } else {
            Ok(())
        }
    }
}

pub struct Reply {
    pub status: u16,
    body: Zeroizing<Vec<u8>>,
}
impl Reply {
    pub fn new(status: u16, body: Vec<u8>) -> Result<Self, ClientError> {
        let body = Zeroizing::new(body);
        if body.len() > MAX_RESPONSE_BYTES {
            return Err(ClientError::ResponseTooLarge);
        }
        Ok(Self { status, body })
    }
    pub fn body(&self) -> &[u8] {
        &self.body
    }
    pub(crate) fn decode<T: DeserializeOwned>(&self) -> Result<T, ClientError> {
        serde_json::from_slice(&self.body).map_err(|_| ClientError::InvalidResponse)
    }
}
impl std::fmt::Debug for Reply {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("SessionReply(<redacted>)")
    }
}

/// Synchronous worker interface. Implementations must bound each request.
pub trait SessionTransport {
    fn request(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        body: Option<&[u8]>,
        access: Option<&str>,
        operation: &Operation,
    ) -> Result<Reply, TransportError>;
}

#[derive(Default)]
pub struct NativeTransport;
impl SessionTransport for NativeTransport {
    fn request(
        &self,
        profile: &ConnectionProfile,
        path: &str,
        body: Option<&[u8]>,
        access: Option<&str>,
        operation: &Operation,
    ) -> Result<Reply, TransportError> {
        let mut control = operation.0.lock().unwrap_or_else(|e| e.into_inner());
        if control.cancelled || control.request.is_some() {
            return Err(TransportError {
                kind: if control.cancelled {
                    ErrorKind::Cancelled
                } else {
                    ErrorKind::Busy
                },
                outcome: RequestOutcome::NotSent,
            });
        }
        let task = GatewayTransport.request(profile, path, body, access)?;
        control.request = Some(task.cancellation());
        drop(control);
        // Uses the existing transport's bounded runtime; no second network runtime.
        let result = futures_executor::block_on(task);
        operation
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .request = None;
        let reply = result?;
        Reply::new(reply.status(), reply.body().to_vec()).map_err(|_| TransportError {
            kind: ErrorKind::ResponseTooLarge,
            outcome: RequestOutcome::ResponseReceived,
        })
    }
}

pub trait Clock {
    fn now(&self) -> f64;
    fn elapsed(&self) -> Duration;
    fn sleep(&self, duration: Duration);
}
pub struct SystemClock(Instant);
impl Default for SystemClock {
    fn default() -> Self {
        Self(Instant::now())
    }
}
impl Clock for SystemClock {
    fn now(&self) -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(f64::NAN, |v| v.as_secs_f64())
    }
    fn elapsed(&self) -> Duration {
        self.0.elapsed()
    }
    fn sleep(&self, duration: Duration) {
        std::thread::sleep(duration);
    }
}
