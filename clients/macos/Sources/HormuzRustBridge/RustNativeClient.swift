import CHormuzRust
import Foundation
import HormuzClientCore

public enum RustBridgeError: Error { case unavailable, closed, busy, invalidProjection }

/// One owner of the shared Rust desktop worker. All custody still executes in
/// this Swift executable through the supplied SessionStore. No credential is
/// part of the JSON display projection or change subscription.
public final class RustNativeClient: @unchecked Sendable {
    private let queue = DispatchQueue(label: "com.hormuz.rust-ui")
    private let state: State

    private init(store: any SessionStore, openBrowser: @escaping @Sendable (URL) -> Bool) {
        state = State(host: CustodyHost(store: store, openBrowser: openBrowser))
    }

    public static func open(directory: URL, store: any SessionStore,
                            openBrowser: @escaping @Sendable (URL) -> Bool) async throws -> RustNativeClient {
        let client = RustNativeClient(store: store, openBrowser: openBrowser)
        try await client.perform { state in
            let host = state.host
            let callbacks = HormuzHost(context: Unmanaged.passUnretained(host).toOpaque(),
                read: custodyRead, write: custodyWrite, delete_record: custodyDelete,
                open_browser: custodyBrowser)
            let bytes = Array(directory.path.utf8)
            state.core = bytes.withUnsafeBufferPointer { hormuz_ui_new($0.baseAddress, $0.count, callbacks) }
            guard state.core != nil, hormuz_ui_abi_version() == 1 else { throw RustBridgeError.unavailable }
        }
        return client
    }

    deinit {
        // Never join a credential worker on the main thread. State/host stay
        // strongly owned until unsubscribe, cancellation and the join finish.
        let state = state
        queue.async { state.close() }
    }

    public func shutdown() async {
        _ = try? await perform { $0.close() }
    }

    public func connect(_ profile: ConnectionProfile) async throws {
        let data = try JSONEncoder().encode(profile.validated())
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            let accepted = data.withUnsafeBytes { hormuz_ui_connect(core, $0.bindMemory(to: UInt8.self).baseAddress, $0.count) }
            guard accepted else { throw RustBridgeError.busy }
        }
    }
    public func disconnect() async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            hormuz_ui_disconnect(core)
        }
    }
    public func retry() async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard hormuz_ui_retry(core) else { throw RustBridgeError.busy }
        }
    }
    public func cancel() async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard hormuz_ui_cancel(core) else { throw RustBridgeError.busy }
        }
    }
    public func refresh() async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard hormuz_ui_refresh(core) else { throw RustBridgeError.busy }
        }
    }
    public enum Visibility: UInt32, Sendable { case hidden, summary, detail }
    public enum Lifecycle: UInt32, Sendable { case sleep, wake, networkChanged, locked, unlocked, quit }
    public func visibility(_ value: Visibility) async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard hormuz_ui_visibility(core, value.rawValue) else { throw RustBridgeError.unavailable }
        }
    }
    public func lifecycle(_ value: Lifecycle) async throws {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard hormuz_ui_lifecycle(core, value.rawValue) else { throw RustBridgeError.unavailable }
        }
    }
    public func snapshot() async throws -> Data {
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            return try copyDisplay(hormuz_ui_snapshot(core))
        }
    }

    /// The shell executes its existing governed launcher. Rust returns only a
    /// currently verified profile; it does not manufacture a command or token.
    public func launch(using action: @Sendable (ConnectionProfile) async throws -> Void) async throws {
        let data = try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            return try copyDisplay(hormuz_ui_launch_profile(core))
        }
        try await action(JSONDecoder().decode(ConnectionProfile.self, from: data))
    }
    public func changeContextSetting(enabled: Bool,
                                    save: @Sendable (Data) async throws -> Void) async throws {
        let data = try await perform { _ in try copyDisplay(hormuz_ui_context_setting(enabled)) }
        try await save(data)
    }

    /// One subscription across all views. Closing it suppresses already queued
    /// main-actor deliveries as well as unregistering the native wake callback.
    public func subscribe(_ receive: @escaping @MainActor @Sendable (Data) -> Void) async throws -> RustNativeSubscription {
        let delivery = Delivery(receive: receive)
        try await perform { state in
            guard let core = state.core else { throw RustBridgeError.closed }
            guard state.subscription == nil else { throw RustBridgeError.busy }
            let wake = WakeContext(delivery: delivery, client: self)
            state.wake = wake
            state.subscription = hormuz_ui_subscribe(core, nativeWake, Unmanaged.passUnretained(wake).toOpaque())
            guard state.subscription != nil else { state.wake = nil; throw RustBridgeError.busy }
        }
        return RustNativeSubscription(delivery: delivery, client: self)
    }

    fileprivate func unsubscribe(_ delivery: Delivery) async {
        delivery.cancel()
        _ = try? await perform { state in
            guard state.wake?.delivery === delivery else { return }
            state.unsubscribe()
        }
    }
    fileprivate func wake(_ delivery: Delivery) {
        queue.async { [weak self, weak delivery] in
            guard let self, let delivery, delivery.isActive,
                  self.state.wake?.delivery === delivery, let core = self.state.core,
                  let bytes = hormuz_ui_take_change(core) else { return }
            guard let data = try? copyDisplay(bytes) else { return }
            Task { @MainActor [weak delivery] in
                guard let delivery, delivery.isActive else { return }
                delivery.receive(data)
            }
        }
    }
    private func perform<T: Sendable>(_ action: @escaping @Sendable (State) throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            let state = state
            queue.async {
                do { continuation.resume(returning: try action(state)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }
}

public final class RustNativeSubscription: @unchecked Sendable {
    private let delivery: Delivery
    private weak var client: RustNativeClient?
    fileprivate init(delivery: Delivery, client: RustNativeClient) { self.delivery = delivery; self.client = client }
    deinit {
        delivery.cancel()
        let delivery = delivery
        if let client { Task { await client.unsubscribe(delivery) } }
    }
    public func cancel() async {
        delivery.cancel()
        await client?.unsubscribe(delivery)
    }
}

private final class State: @unchecked Sendable {
    let host: CustodyHost
    var core: OpaquePointer?
    var subscription: OpaquePointer?
    var wake: WakeContext?
    init(host: CustodyHost) { self.host = host }
    func unsubscribe() {
        wake?.delivery.cancel()
        if let subscription { hormuz_ui_unsubscribe(subscription) }
        subscription = nil
        wake = nil
    }
    func close() {
        unsubscribe()
        if let core { hormuz_ui_free(core) }
        core = nil
    }
}
fileprivate final class Delivery: @unchecked Sendable {
    private let lock = NSLock()
    private var active = true
    let receive: @MainActor @Sendable (Data) -> Void
    init(receive: @escaping @MainActor @Sendable (Data) -> Void) { self.receive = receive }
    var isActive: Bool { lock.lock(); defer { lock.unlock() }; return active }
    func cancel() { lock.lock(); active = false; lock.unlock() }
}
private final class WakeContext: @unchecked Sendable {
    let delivery: Delivery
    weak var client: RustNativeClient?
    init(delivery: Delivery, client: RustNativeClient) { self.delivery = delivery; self.client = client }
}
private func nativeWake(_ pointer: UnsafeMutableRawPointer?) {
    guard let pointer else { return }
    let context = Unmanaged<WakeContext>.fromOpaque(pointer).takeUnretainedValue()
    context.client?.wake(context.delivery)
}
private func copyDisplay(_ bytes: OpaquePointer?) throws -> Data {
    guard let bytes else { throw RustBridgeError.unavailable }
    defer { hormuz_ui_bytes_free(bytes) }
    let count = hormuz_ui_bytes_length(bytes)
    guard count <= 128 * 1024 else { throw RustBridgeError.invalidProjection }
    var data = Data(count: count)
    guard data.withUnsafeMutableBytes({ hormuz_ui_bytes_copy(bytes, $0.bindMemory(to: UInt8.self).baseAddress, $0.count) }) else {
        throw RustBridgeError.invalidProjection
    }
    return data
}

private final class CustodyHost: @unchecked Sendable {
    let store: any SessionStore
    let openBrowser: @Sendable (URL) -> Bool
    init(store: any SessionStore, openBrowser: @escaping @Sendable (URL) -> Bool) {
        self.store = store; self.openBrowser = openBrowser
    }
}
private func custody(_ pointer: UnsafeMutableRawPointer?) -> CustodyHost? {
    pointer.map { Unmanaged<CustodyHost>.fromOpaque($0).takeUnretainedValue() }
}
private func custodyRead(_ pointer: UnsafeMutableRawPointer?, _ output: UnsafeMutablePointer<OpaquePointer?>?) -> Int32 {
    guard let host = custody(pointer), let output else { return -1 }
    output.pointee = nil
    do {
        guard let record = try host.store.load() else { return 0 }
        var data = try JSONEncoder().encode(record.validated(for: record.profile))
        defer { data.resetBytes(in: data.startIndex..<data.endIndex) }
        output.pointee = data.withUnsafeBytes { hormuz_ui_secret_new($0.bindMemory(to: UInt8.self).baseAddress, $0.count) }
        return output.pointee == nil ? -1 : 1
    } catch { return -1 }
}
private func custodyWrite(_ pointer: UnsafeMutableRawPointer?, _ secret: OpaquePointer?) -> Int32 {
    guard let host = custody(pointer), let secret else { return -1 }
    let count = hormuz_ui_secret_length(secret)
    guard count > 0, count < 32_768 else { return -1 }
    var data = Data(count: count)
    defer { data.resetBytes(in: data.startIndex..<data.endIndex) }
    guard data.withUnsafeMutableBytes({ hormuz_ui_secret_copy(secret, $0.bindMemory(to: UInt8.self).baseAddress, $0.count) }) else { return -1 }
    do {
        let record = try JSONDecoder().decode(SessionRecord.self, from: data)
        try host.store.save(record.validated(for: record.profile))
        return 0
    } catch { return -1 }
}
private func custodyDelete(_ pointer: UnsafeMutableRawPointer?) -> Int32 {
    guard let host = custody(pointer) else { return -1 }
    do { try host.store.delete(); return 0 } catch { return -1 }
}
private func custodyBrowser(_ pointer: UnsafeMutableRawPointer?, _ bytes: UnsafePointer<UInt8>?, _ count: Int) -> Int32 {
    guard let host = custody(pointer), let bytes, count > 0, count <= 16_384,
          let text = String(bytes: UnsafeBufferPointer(start: bytes, count: count), encoding: .utf8),
          let url = URL(string: text) else { return -1 }
    return host.openBrowser(url) ? 0 : -1
}
