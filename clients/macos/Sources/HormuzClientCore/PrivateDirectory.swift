import Darwin
import Foundation

/// Only non-secret profile/configuration data lives here. Reject unsafe existing
/// files instead of chmod-ing or replacing something we do not own.
public final class PrivateDirectory: @unchecked Sendable {
    public let root: URL
    public static var defaultURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Hormuz", isDirectory: true)
    }

    public init(root: URL = PrivateDirectory.defaultURL, create: Bool = true) throws {
        self.root = root.standardizedFileURL
        if create, mkdir(self.root.path, 0o700) != 0, errno != EEXIST { throw ClientError.storageUnavailable }
        if !create, !(try verifiedExists()) { return }
        try validateRoot()
    }

    public var exists: Bool {
        (try? verifiedExists()) ?? false
    }

    func verifiedExists() throws -> Bool {
        var info = stat()
        let result = lstat(root.path, &info)
        return try Self.presence(result: result, error: errno)
    }

    static func presence(result: Int32, error: Int32) throws -> Bool {
        if result == 0 { return true }
        guard error == ENOENT else { throw ClientError.storageUnavailable }
        return false
    }

    func requireSetupWritable() throws {
        guard try read(NativeOwnership.intentName) == nil else { throw ClientError.removalPending }
    }

    public func fileURL(_ name: String) throws -> URL {
        guard !name.isEmpty, name.count <= 128, name != ".", name != "..",
              name.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "._-".contains($0)) })
        else { throw ClientError.unsafeStorage }
        try validateRoot()
        return root.appendingPathComponent(name)
    }

    public func read(_ name: String) throws -> Data? {
        let path = try fileURL(name).path
        let fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        if fd < 0 {
            if errno == ENOENT { return nil }
            throw ClientError.unsafeStorage
        }
        defer { close(fd) }
        try validateFile(fd)
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
        do {
            let data = try handle.read(upToCount: 1_048_577) ?? Data()
            guard data.count <= 1_048_576 else { throw ClientError.unsafeStorage }
            return data
        } catch let error as ClientError { throw error }
        catch { throw ClientError.storageUnavailable }
    }

    /// Atomically install a new file only when the displaced snapshot matches
    /// the preview. Callers hold the process lock; path-level external edits
    /// that arrive before the exchange are restored instead of overwritten.
    public func write(_ data: Data, to name: String, expected: Data?, executable: Bool = false) throws {
        try writeAtomically(data, to: name, expected: expected, executable: executable)
    }

    func writeAtomically(_ data: Data, to name: String, expected: Data?, executable: Bool = false,
                         beforeExchange: (() throws -> Void)? = nil) throws {
        guard data.count <= 1_048_576 else { throw ClientError.storageUnavailable }
        let target = try fileURL(name)
        let temporaryName = ".write-" + UUID().uuidString
        let temporary = try fileURL(temporaryName)
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, executable ? 0o700 : 0o600)
        guard fd >= 0 else { throw ClientError.storageUnavailable }
        var removeTemporary = true
        defer {
            close(fd)
            if removeTemporary { unlink(temporary.path) }
        }
        do {
            try FileHandle(fileDescriptor: fd, closeOnDealloc: false).write(contentsOf: data)
            guard fsync(fd) == 0 else { throw ClientError.storageUnavailable }
            try beforeExchange?()
            if expected == nil {
                guard renamex_np(temporary.path, target.path, UInt32(RENAME_EXCL)) == 0 else {
                    if errno == EEXIST { throw ClientError.configurationChanged }
                    throw ClientError.storageUnavailable
                }
                removeTemporary = false
                return
            }

            guard renamex_np(temporary.path, target.path, UInt32(RENAME_SWAP)) == 0 else {
                if errno == ENOENT { throw ClientError.configurationChanged }
                throw ClientError.storageUnavailable
            }
            do {
                guard try read(temporaryName) == expected else {
                    throw ClientError.configurationChanged
                }
            } catch {
                let comparisonError = error
                guard renamex_np(temporary.path, target.path, UInt32(RENAME_SWAP)) == 0 else {
                    // Preserve both files if the rollback itself cannot complete.
                    removeTemporary = false
                    throw ClientError.storageUnavailable
                }
                throw comparisonError
            }
        } catch let error as ClientError { throw error }
        catch { throw ClientError.storageUnavailable }
    }

    public func lock(name: String = "connection.lock", timeout: TimeInterval = 10) async throws -> ProfileLock {
        let path = try fileURL(name).path
        let fd = open(path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw ClientError.unsafeStorage }
        do {
            try validateFile(fd)
            let deadline = ContinuousClock.now + .milliseconds(Int(timeout * 1000))
            while flock(fd, LOCK_EX | LOCK_NB) != 0 {
                guard errno == EWOULDBLOCK || errno == EAGAIN else { throw ClientError.storageUnavailable }
                guard ContinuousClock.now < deadline else { throw ClientError.profileBusy }
                try await Task.sleep(for: .milliseconds(50))
            }
            return ProfileLock(fd: fd)
        } catch { close(fd); throw error }
    }

    public func loadProfile() throws -> ConnectionProfile? {
        guard let data = try read("profile.json") else { return nil }
        do { return try JSONDecoder().decode(ConnectionProfile.self, from: data).validated() }
        catch let error as ClientError { throw error }
        catch { throw ClientError.invalidProfile }
    }

    public func saveProfile(_ profile: ConnectionProfile) throws {
        try requireSetupWritable()
        let value = try profile.validated()
        let data = try JSONEncoder().encode(value)
        try write(data, to: "profile.json", expected: read("profile.json"))
        try NativeOwnership.record([NativeOwnedFile(name: "profile.json", digest: NativeOwnership.digest(data),
                                                    profileID: profile.id, kind: "profile")], directory: self)
    }

    /// Only enumerates a bounded private directory. No recursive traversal and
    /// no arbitrary child content is read to decide whether it belongs to us.
    func names() throws -> [String] {
        try validateRoot()
        let names = try FileManager.default.contentsOfDirectory(atPath: root.path)
        guard names.count <= 512 else { throw ClientError.unsafeStorage }
        return names.sorted()
    }

    func directoryIdentity() throws -> String {
        try validateRoot()
        var info = stat()
        guard lstat(root.path, &info) == 0 else { throw ClientError.unsafeStorage }
        return String(info.st_dev) + ":" + String(info.st_ino)
    }

    /// Commit removal-journal namespace changes before deleting credentials or
    /// advancing cleanup. A failure keeps dependent effects closed.
    func syncMetadata() throws {
        try validateRoot()
        let descriptor = open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw ClientError.storageUnavailable }
        defer { close(descriptor) }
        var pinned = stat(), current = stat()
        guard fstat(descriptor, &pinned) == 0, lstat(root.path, &current) == 0,
              pinned.st_dev == current.st_dev, pinned.st_ino == current.st_ino,
              pinned.st_uid == getuid(), pinned.st_mode & 0o077 == 0,
              fsync(descriptor) == 0 else { throw ClientError.storageUnavailable }
    }

    /// Stage the exact snapshot through a pinned directory descriptor. A crash
    /// leaves a deterministic stage name from the durable removal intent.
    @discardableResult
    func removeExpected(_ name: String, digest: String, beforeStage: (() throws -> Void)? = nil) throws -> Bool {
        _ = try fileURL(name)
        let stage = ".remove-" + NativeOwnership.digest(Data(name.utf8))
        let rootFD = open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard rootFD >= 0 else { throw ClientError.unsafeStorage }
        defer { close(rootFD) }
        var pinned = stat(); var current = stat()
        guard fstat(rootFD, &pinned) == 0, lstat(root.path, &current) == 0,
              pinned.st_dev == current.st_dev, pinned.st_ino == current.st_ino,
              pinned.st_uid == getuid(), pinned.st_mode & 0o077 == 0 else { throw ClientError.unsafeStorage }
        func readAt(_ child: String) throws -> Data? {
            let fd = openat(rootFD, child, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
            if fd < 0 {
                if errno == ENOENT { return nil }
                throw ClientError.unsafeStorage
            }
            defer { close(fd) }
            try validateFile(fd)
            return try FileHandle(fileDescriptor: fd, closeOnDealloc: false).read(upToCount: 1_048_577)
        }
        let existingStage = try readAt(stage)
        let existing = try readAt(name)
        if let existingStage {
            guard existing == nil, NativeOwnership.digest(existingStage) == digest else {
                throw ClientError.configurationChanged
            }
        } else {
            guard let existing else { return false }
            guard NativeOwnership.digest(existing) == digest else { throw ClientError.configurationChanged }
            try beforeStage?()
            guard lstat(root.path, &current) == 0, current.st_ino == pinned.st_ino,
                  current.st_dev == pinned.st_dev else { throw ClientError.configurationChanged }
            guard renameatx_np(rootFD, name, rootFD, stage, UInt32(RENAME_EXCL)) == 0 else {
                throw ClientError.configurationChanged
            }
            do {
                guard let displaced = try readAt(stage), NativeOwnership.digest(displaced) == digest else {
                    throw ClientError.configurationChanged
                }
            } catch {
                // Exclusive restore cannot overwrite a newer external file.
                guard renameatx_np(rootFD, stage, rootFD, name, UInt32(RENAME_EXCL)) == 0 else {
                    throw ClientError.removalRecoveryRequired
                }
                throw error
            }
        }
        guard unlinkat(rootFD, stage, 0) == 0, fsync(rootFD) == 0 else { throw ClientError.storageUnavailable }
        return true
    }

    private func validateRoot() throws {
        var info = stat()
        guard lstat(root.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { throw ClientError.unsafeStorage }
    }

    private func validateFile(_ fd: Int32) throws {
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0, info.st_size <= 1_048_576
        else { throw ClientError.unsafeStorage }
    }
}

public final class ProfileLock {
    private var fd: Int32
    fileprivate init(fd: Int32) { self.fd = fd }
    public func unlock() { if fd >= 0 { flock(fd, LOCK_UN); close(fd); fd = -1 } }
    deinit { unlock() }
}
