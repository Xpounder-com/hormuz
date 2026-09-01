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

    public init(root: URL = PrivateDirectory.defaultURL) throws {
        self.root = root.standardizedFileURL
        if mkdir(self.root.path, 0o700) != 0, errno != EEXIST { throw ClientError.storageUnavailable }
        try validateRoot()
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

    /// Compare with the preview snapshot, then atomically replace an owned file.
    /// Callers hold the process lock; a concurrent non-Hormuz edit fails closed.
    public func write(_ data: Data, to name: String, expected: Data?, executable: Bool = false) throws {
        guard data.count <= 1_048_576 else { throw ClientError.storageUnavailable }
        guard try read(name) == expected else { throw ClientError.configurationChanged }
        let target = try fileURL(name)
        let temporary = try fileURL(".write-" + UUID().uuidString)
        let fd = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, executable ? 0o700 : 0o600)
        guard fd >= 0 else { throw ClientError.storageUnavailable }
        defer { close(fd); unlink(temporary.path) }
        do {
            try FileHandle(fileDescriptor: fd, closeOnDealloc: false).write(contentsOf: data)
            guard fsync(fd) == 0 else { throw ClientError.storageUnavailable }
            guard try read(name) == expected else { throw ClientError.configurationChanged }
            guard rename(temporary.path, target.path) == 0 else { throw ClientError.storageUnavailable }
        } catch let error as ClientError { throw error }
        catch { throw ClientError.storageUnavailable }
    }

    public func lock(timeout: TimeInterval = 10) async throws -> ProfileLock {
        let path = try fileURL("connection.lock").path
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
        let value = try profile.validated()
        let data = try JSONEncoder().encode(value)
        try write(data, to: "profile.json", expected: read("profile.json"))
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
