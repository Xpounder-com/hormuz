import Darwin
import XCTest
@testable import HormuzClientCore

final class RelayOwnerTests: XCTestCase {
    private func connect(_ url: URL) throws -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw ClientError.storageUnavailable }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: Array(url.path.utf8) + [0]) }
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0 else { close(fd); throw ClientError.storageUnavailable }
        return fd
    }

    private func assertClosed(_ fd: Int32) {
        let deadline = ContinuousClock.now + .seconds(2)
        var byte: UInt8 = 0
        while true {
            let result = recv(fd, &byte, 1, MSG_DONTWAIT)
            if result == 0 { return }
            guard result < 0, errno == EAGAIN, ContinuousClock.now < deadline else {
                XCTFail("Expected owner EOF within the deadline")
                return
            }
            usleep(10_000)
        }
    }

    func testLeaseIsPrivateAndStaysOpenUntilOwnerStops() throws {
        let owner = try RelayOwner()
        defer { owner.stop() }
        let fd = try connect(owner.socketURL)
        defer { close(fd) }
        let parent = owner.socketURL.deletingLastPathComponent()
        let attributes = try FileManager.default.attributesOfItem(atPath: parent.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o700)
        var byte: UInt8 = 0
        XCTAssertEqual(recv(fd, &byte, 1, MSG_DONTWAIT), -1)
        XCTAssertEqual(errno, EAGAIN)
        owner.stop()
        assertClosed(fd)
        XCTAssertFalse(FileManager.default.fileExists(atPath: parent.path))
        XCTAssertThrowsError(try connect(owner.socketURL))
        owner.stop() // Idempotent, never targets another owner's socket.
    }

    func testOwnerDeinitClosesAllLeasesAndDoesNotAffectAnotherOwner() throws {
        var owner: RelayOwner? = try RelayOwner()
        let other = try RelayOwner()
        defer { other.stop() }
        let fd = try connect(owner!.socketURL)
        let second = try connect(owner!.socketURL)
        defer { close(fd); close(second) }
        owner = nil
        assertClosed(fd)
        assertClosed(second)
        let unrelated = try connect(other.socketURL)
        defer { close(unrelated) }
        var byte: UInt8 = 0
        XCTAssertEqual(recv(unrelated, &byte, 1, MSG_DONTWAIT), -1)
        XCTAssertEqual(errno, EAGAIN)
    }

    func testDrainRejectsNewLaunchesWithoutInterruptingExistingClientsAndCanResume() throws {
        let owner = try RelayOwner()
        defer { owner.stop() }
        let active = try connect(owner.socketURL)
        defer { close(active) }
        XCTAssertEqual(owner.activeClientCount, 1)
        XCTAssertEqual(owner.beginDrain(), 1)
        var byte: UInt8 = 0
        XCTAssertEqual(recv(active, &byte, 1, MSG_DONTWAIT), -1)
        XCTAssertEqual(errno, EAGAIN) // Panel close / waiting to quit retains traffic.
        let rejected = try connect(owner.socketURL)
        defer { close(rejected) }
        assertClosed(rejected)
        XCTAssertEqual(owner.activeClientCount, 1)
        owner.resumeAdmissions()
        let next = try connect(owner.socketURL)
        defer { close(next) }
        XCTAssertEqual(owner.activeClientCount, 2)
        owner.stop() // Explicit forced quit releases both leases.
        assertClosed(active)
        assertClosed(next)
        XCTAssertEqual(owner.activeClientCount, 0)
    }
}
