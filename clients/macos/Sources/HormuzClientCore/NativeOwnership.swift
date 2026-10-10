import CryptoKit
import Foundation

struct NativeOwnedFile: Codable, Equatable, Sendable {
    let name: String
    let digest: String
    let profileID: UUID
    let kind: String
}

struct NativeOwnershipManifest: Codable {
    let schemaVersion: Int
    var files: [NativeOwnedFile]
}

enum NativeOwnership {
    static let fileName = "native-owned-files.json"
    static let intentName = "native-removal.json"
    static let retainedFileName = "native-retained-files.json"
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func load(_ directory: PrivateDirectory, name: String = fileName) throws -> [NativeOwnedFile] {
        guard let data = try directory.read(name) else { return [] }
        guard data.count <= 65_536,
              let value = try? JSONDecoder().decode(NativeOwnershipManifest.self, from: data),
              value.schemaVersion == 1, value.files.count <= 256,
              Set(value.files.map(\.name)).count == value.files.count else { throw ClientError.unsafeStorage }
        for entry in value.files {
            _ = try directory.fileURL(entry.name)
            guard entry.digest.count == 64,
                  entry.digest.allSatisfy({ $0.isASCII && ($0.isNumber || "abcdef".contains($0)) }),
                  ["profile", "launcher", "preference"].contains(entry.kind),
                  validName(entry) else { throw ClientError.unsafeStorage }
        }
        return value.files
    }

    private static func validName(_ entry: NativeOwnedFile) -> Bool {
        let key = entry.profileID.uuidString.lowercased()
        switch entry.kind {
        case "profile": return entry.name == "profile.json"
        case "preference": return entry.name == "context-optimization-" + key + ".json"
        case "launcher": return ["codex-", "claude-code-"].contains { entry.name == $0 + key + ".command" }
        default: return false
        }
    }

    static func allowed(_ entry: NativeOwnedFile) -> Bool { validName(entry) }

    static func record(_ entries: [NativeOwnedFile], directory: PrivateDirectory, name: String = fileName) throws {
        var existing = try load(directory, name: name)
        for entry in entries {
            guard validName(entry) else { throw ClientError.unsafeStorage }
            existing.removeAll { $0.name == entry.name }
            existing.append(entry)
        }
        guard existing.count <= 256 else { throw ClientError.storageUnavailable }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(NativeOwnershipManifest(schemaVersion: 1, files: existing.sorted { $0.name < $1.name }))
        try directory.write(data, to: name, expected: directory.read(name))
    }

    static func sessionFingerprint(_ record: SessionRecord?) throws -> String? {
        guard var record else { return nil }
        // Session state changes to revocationPending during retry; identity and
        // credentials must stay exact. Only this digest enters a cleanup intent.
        record.state = .active
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return digest(try encoder.encode(record.validated(for: record.profile)))
    }
}
