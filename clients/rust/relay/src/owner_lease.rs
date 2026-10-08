//! A private, same-UID app lease. Panel visibility never owns this socket.
#![forbid(unsafe_code)]

use hormuz_client_relay::RelayError;
use rustix::io::{fcntl_getfd, fcntl_setfd, FdFlags};
use rustix::net::{connect, socket, AddressFamily, SocketAddrUnix, SocketType};
use std::io::Read;
use std::os::unix::{
    fs::{FileTypeExt, MetadataExt},
    net::UnixStream,
};
use std::path::Path;

pub(crate) struct OwnerLease(UnixStream);
impl OwnerLease {
    pub(crate) fn open(path: &Path) -> Result<Self, RelayError> {
        if !path.is_absolute() {
            return Err(RelayError::InvalidConfiguration);
        }
        let parent = path.parent().ok_or(RelayError::InvalidConfiguration)?;
        let directory = parent
            .symlink_metadata()
            .map_err(|_| RelayError::InvalidConfiguration)?;
        let endpoint = path
            .symlink_metadata()
            .map_err(|_| RelayError::InvalidConfiguration)?;
        let uid = rustix::process::geteuid().as_raw();
        if !directory.is_dir()
            || directory.mode() & 0o777 != 0o700
            || directory.uid() != uid
            || !endpoint.file_type().is_socket()
            || endpoint.uid() != uid
        {
            return Err(RelayError::InvalidConfiguration);
        }
        // Refuse a full backlog instead of blocking shutdown on connect. Only
        // this owned end is nonblocking; the app keeps its normal listener.
        let address = SocketAddrUnix::new(path).map_err(|_| RelayError::InvalidConfiguration)?;
        let socket = socket(AddressFamily::UNIX, SocketType::STREAM, None)
            .map_err(|_| RelayError::InvalidConfiguration)?;
        let flags = fcntl_getfd(&socket).map_err(|_| RelayError::InvalidConfiguration)?;
        fcntl_setfd(&socket, flags | FdFlags::CLOEXEC)
            .map_err(|_| RelayError::InvalidConfiguration)?;
        let stream = UnixStream::from(socket);
        stream
            .set_nonblocking(true)
            .map_err(|_| RelayError::InvalidConfiguration)?;
        connect(&stream, &address).map_err(|_| RelayError::InvalidConfiguration)?;
        #[cfg(target_os = "linux")]
        if rustix::net::sockopt::socket_peercred(&stream)
            .map_err(|_| RelayError::InvalidConfiguration)?
            .uid
            .as_raw()
            != uid
        {
            return Err(RelayError::InvalidConfiguration);
        }
        // A connected backlog entry is not yet an app-owned invocation. The
        // Linux shell acknowledges only a same-UID peer it will retain while
        // still launching; without that acceptance no custody/discovery runs.
        #[cfg(target_os = "linux")]
        await_acceptance(&stream, std::time::Duration::from_secs(3))?;
        Ok(Self(stream))
    }

    pub(crate) fn stopped(&self) -> bool {
        match (&self.0).read(&mut [0]) {
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => false,
            // Expected EOF, malformed data or a broken channel all stop work.
            _ => true,
        }
    }
}

#[cfg(any(target_os = "linux", test))]
fn await_acceptance(stream: &UnixStream, timeout: std::time::Duration) -> Result<(), RelayError> {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        let mut acknowledgement = [0];
        match (&*stream).read(&mut acknowledgement) {
            Ok(1) if acknowledgement[0] == 1 => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                if std::time::Instant::now() >= deadline {
                    return Err(RelayError::InvalidConfiguration);
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            _ => return Err(RelayError::InvalidConfiguration),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::{
        fs::{symlink, PermissionsExt},
        net::UnixListener,
    };

    fn accepted_lease(listener: &UnixListener, path: &Path) -> (OwnerLease, UnixStream) {
        let listener = listener.try_clone().unwrap();
        let accepting = std::thread::spawn(move || {
            let (mut owner, _) = listener.accept().unwrap();
            #[cfg(target_os = "linux")]
            owner.write_all(&[1]).unwrap();
            #[cfg(not(target_os = "linux"))]
            let _ = &mut owner;
            owner
        });
        let lease = OwnerLease::open(path).unwrap();
        (lease, accepting.join().unwrap())
    }

    #[test]
    fn owner_eof_and_unexpected_data_stop_the_invocation() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let path = directory.path().join("lease");
        let listener = UnixListener::bind(&path).unwrap();
        let (lease, owner) = accepted_lease(&listener, &path);
        assert!(fcntl_getfd(&lease.0).unwrap().contains(FdFlags::CLOEXEC));
        assert!(!lease.stopped());
        drop(owner);
        assert!(lease.stopped());
        let (lease, mut owner) = accepted_lease(&listener, &path);
        owner.write_all(b"unexpected").unwrap();
        assert!(lease.stopped());
    }

    #[test]
    fn acceptance_is_bounded_and_requires_the_acknowledgement_byte() {
        let (peer, mut owner) = UnixStream::pair().unwrap();
        peer.set_nonblocking(true).unwrap();
        let timeout = std::time::Duration::from_millis(30);
        assert!(await_acceptance(&peer, timeout).is_err());
        owner.write_all(&[1]).unwrap();
        assert!(await_acceptance(&peer, timeout).is_ok());
        owner.write_all(&[2]).unwrap();
        assert!(await_acceptance(&peer, timeout).is_err());
        drop(owner);
        assert!(await_acceptance(&peer, timeout).is_err());
    }

    #[test]
    fn public_parent_and_symlink_endpoints_fail_closed() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let path = directory.path().join("lease");
        let _listener = UnixListener::bind(&path).unwrap();
        let link = directory.path().join("link");
        symlink(&path, &link).unwrap();
        assert!(OwnerLease::open(&link).is_err());
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(OwnerLease::open(&path).is_err());
        assert!(OwnerLease::open(Path::new("relative")).is_err());
    }
}
