import Darwin
import Foundation

/// A process-lifetime lease, independent of every window and panel. The native
/// relay stops when its connection closes, including an abrupt app exit. No
/// PID files, credentials, or request bytes are exchanged on this socket.
public final class RelayOwner: @unchecked Sendable {
    public let socketURL: URL
    private let root: URL
    private let queue = DispatchQueue(label: "com.hormuz.relay-owner")
    private let queueKey = DispatchSpecificKey<Bool>()
    private var listener: DispatchSourceRead?
    private var connections: [Int32: DispatchSourceRead] = [:]

    public init() throws {
        // Keep the Unix socket pathname below Darwin's 104-byte limit, even
        // when the user's normal application-support path is long.
        root = URL(fileURLWithPath: "/private/tmp/hormuz-owner-" + UUID().uuidString)
        socketURL = root.appendingPathComponent("lease")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            try? FileManager.default.removeItem(at: root)
            throw ClientError.storageUnavailable
        }
        var succeeded = false
        defer {
            if !succeeded {
                close(fd)
                try? FileManager.default.removeItem(at: root)
            }
        }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        let path = Array(socketURL.path.utf8) + [0]
        guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            throw ClientError.storageUnavailable
        }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: path) }
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0, chmod(socketURL.path, 0o600) == 0,
              fcntl(fd, F_SETFD, FD_CLOEXEC) == 0,
              fcntl(fd, F_SETFL, O_NONBLOCK) == 0, listen(fd, 16) == 0 else {
            throw ClientError.storageUnavailable
        }
        queue.setSpecific(key: queueKey, value: true)
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.acceptConnections(fd) }
        source.setCancelHandler { close(fd) }
        listener = source
        succeeded = true
        source.resume()
    }

    public func stop() {
        let closeConnections = {
            self.listener?.cancel()
            self.listener = nil
            for source in self.connections.values { source.cancel() }
            self.connections.removeAll()
        }
        if DispatchQueue.getSpecific(key: queueKey) != nil { closeConnections() }
        else { queue.sync(execute: closeConnections) }
        // Only this instance's uniquely created directory is removed.
        try? FileManager.default.removeItem(at: root)
    }

    deinit { stop() }

    private func acceptConnections(_ fd: Int32) {
        guard listener != nil else { return }
        while true {
            let connection = accept(fd, nil, nil)
            guard connection >= 0 else { return }
            guard connections.count < 16,
                  fcntl(connection, F_SETFD, FD_CLOEXEC) == 0,
                  fcntl(connection, F_SETFL, O_NONBLOCK) == 0 else {
                close(connection)
                continue
            }
            let source = DispatchSource.makeReadSource(fileDescriptor: connection, queue: queue)
            source.setEventHandler { [weak self] in
                // A relay never sends data. EOF or unexpected data releases
                // the lease; ended sessions do not accumulate descriptors.
                self?.connections.removeValue(forKey: connection)?.cancel()
            }
            source.setCancelHandler { close(connection) }
            connections[connection] = source
            source.resume()
        }
    }
}
