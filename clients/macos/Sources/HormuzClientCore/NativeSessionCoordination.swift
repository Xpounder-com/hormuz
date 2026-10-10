import CryptoKit
import Foundation

/// One stable gate for the shared Keychain namespace. Directory-local
/// connection.lock remains the second lock so default-root legacy clients
/// also serialize their session operations. Never unlink either lock inode.
public final class NativeSessionCoordination: @unchecked Sendable {
    private let directory: PrivateDirectory?
    private let name: String
    private let memory: MemorySessionGate?

    public init(directory: PrivateDirectory, name: String = "native-session.lock") {
        self.directory = directory
        self.name = name
        self.memory = nil
    }

    private init(memory: MemorySessionGate) {
        self.directory = nil
        self.name = "native-session.lock"
        self.memory = memory
    }

    static func forStore(_ store: any SessionStore, directory: PrivateDirectory) throws -> NativeSessionCoordination {
        guard let namespace = store.coordinationNamespace else {
            // In-memory stores never inspect or write the user's real caches.
            return NativeSessionCoordination(memory: MemorySessionGate.forStore(store))
        }
        let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("com.xpounder.hormuz.native", isDirectory: true)
        let digest = NativeOwnership.digest(Data(namespace.utf8))
        return NativeSessionCoordination(directory: try PrivateDirectory(root: cache),
                                         name: "session-" + digest + ".lock")
    }

    func lock(connection: PrivateDirectory, allowAbsent: Bool = false) async throws -> NativeSessionLock {
        let shared = try await lockShared()
        do {
            // Absence is observed after acquiring the same namespace gate as
            // sign-in. Never create an absent state root merely to verify it.
            if allowAbsent, !(try connection.verifiedExists()) { return NativeSessionLock(shared: shared, local: nil) }
            let local = try await connection.lock()
            return NativeSessionLock(shared: shared, local: local)
        } catch {
            shared.unlock()
            throw error
        }
    }

    private func lockShared() async throws -> NativeSessionSharedLock {
        if let directory {
            let lock = try await directory.lock(name: name)
            return NativeSessionSharedLock { lock.unlock() }
        }
        guard let memory else { throw ClientError.storageUnavailable }
        return try await memory.lock()
    }
}

final class NativeSessionLock {
    private let shared: NativeSessionSharedLock
    private let local: ProfileLock?
    fileprivate init(shared: NativeSessionSharedLock, local: ProfileLock?) { self.shared = shared; self.local = local }
    func unlock() { local?.unlock(); shared.unlock() }
    deinit { unlock() }
}

fileprivate final class NativeSessionSharedLock {
    private var release: (() -> Void)?
    init(_ release: @escaping () -> Void) { self.release = release }
    func unlock() { let action = release; release = nil; action?() }
    deinit { unlock() }
}

/// Non-Keychain stores share an in-process gate by store identity. Weak
/// registry entries cannot retain stores, and no user cache/state is touched.
private final class MemorySessionGate {
    private final class Entry {
        weak var store: AnyObject?
        let gate = MemorySessionGate()
        init(store: AnyObject) { self.store = store }
    }
    private static let registryMutex = NSLock()
    private static var registry: [ObjectIdentifier: Entry] = [:]
    private let mutex = NSLock()
    private var held = false

    static func forStore(_ store: any SessionStore) -> MemorySessionGate {
        registryMutex.lock(); defer { registryMutex.unlock() }
        registry = registry.filter { $0.value.store != nil }
        let key = ObjectIdentifier(store)
        if let entry = registry[key] { return entry.gate }
        let entry = Entry(store: store); registry[key] = entry
        return entry.gate
    }
    private func take() -> Bool {
        mutex.lock(); defer { mutex.unlock() }
        guard !held else { return false }; held = true; return true
    }
    private func release() { mutex.lock(); held = false; mutex.unlock() }
    func lock() async throws -> NativeSessionSharedLock {
        let deadline = ContinuousClock.now + .seconds(10)
        while !take() {
            try Task.checkCancellation()
            guard ContinuousClock.now < deadline else { throw ClientError.profileBusy }
            try await Task.sleep(for: .milliseconds(20))
        }
        return NativeSessionSharedLock { self.release() }
    }
}
