import Darwin
import Foundation

/// Metadata-only discovery, never termination. Old binaries do not honor the
/// new shared gate. Detectable other native apps/helpers must be closed first.
enum NativeRemovalProcessSafety {
    static func check() throws {
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_ALL]
        var size = 0
        guard sysctl(&mib, UInt32(mib.count), nil, &size, nil, 0) == 0,
              size > 0, size <= 16 * 1024 * 1024 else { throw ClientError.profileBusy }
        let memory = UnsafeMutableRawPointer.allocate(byteCount: size, alignment: MemoryLayout<kinfo_proc>.alignment)
        defer { memory.deallocate() }
        guard sysctl(&mib, UInt32(mib.count), memory, &size, nil, 0) == 0 else { throw ClientError.profileBusy }
        let entries = memory.bindMemory(to: kinfo_proc.self, capacity: size / MemoryLayout<kinfo_proc>.stride)
        for index in 0..<(size / MemoryLayout<kinfo_proc>.stride) {
            let entry = entries[index]
            guard entry.kp_eproc.e_ucred.cr_uid == getuid(), entry.kp_proc.p_pid != getpid() else { continue }
            var buffer = [CChar](repeating: 0, count: 4096)
            let count = proc_pidpath(entry.kp_proc.p_pid, &buffer, UInt32(buffer.count))
            guard count > 0 else {
                // A vanished process is harmless; an unreadable live process
                // cannot be positively qualified as an unrelated owner.
                if kill(entry.kp_proc.p_pid, 0) == 0 { throw ClientError.profileBusy }
                continue
            }
            let path = String(cString: buffer)
            guard let range = path.range(of: ".app/Contents/") else { continue }
            let bundlePath = String(path[..<range.lowerBound]) + ".app"
            let info = URL(fileURLWithPath: bundlePath).appendingPathComponent("Contents/Info.plist")
            guard let data = boundedInfo(info),
                  let object = try? PropertyListSerialization.propertyList(from: data, format: nil),
                  let plist = object as? [String: Any],
                  let identifier = plist["CFBundleIdentifier"] as? String,
                  ["com.xpounder.hormuz", "com.hormuz.mac.local"].contains(identifier) else { continue }
            throw ClientError.profileBusy
        }
    }

    private static func boundedInfo(_ url: URL) -> Data? {
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size >= 0, info.st_size <= 65_536 else { return nil }
        return try? FileHandle(fileDescriptor: fd, closeOnDealloc: false).read(upToCount: 65_536)
    }
}
