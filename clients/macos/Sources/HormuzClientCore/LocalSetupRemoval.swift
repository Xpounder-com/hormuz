import Foundation

public struct LocalSetupRemovalPreview: Codable, Sendable {
    public let schema_id = "hormuz.native-removal-preview"
    public let schema_version = 1
    public let preview_token: String
    public let generated_files: Int
    public let retained_files: Int
    public let has_session: Bool
    public let pending: Bool
}

public struct LocalSetupRemovalResult: Codable, Sendable {
    public let schema_id = "hormuz.native-removal-result"
    public let schema_version = 1
    public let status: String
    public let server_revocation_confirmed: Bool
    public let session_was_present: Bool
    public let keychain_session_absent: Bool
    /// Selected, unchanged manifest-owned setup; edited/unowned files are
    /// reported separately as retained, never silently claimed to be absent.
    public let generated_setup_absent: Bool
    public let removed_files: Int
    public let retained_files: Int
    public let coordination_locks_retained = true
    public let appearance_reset: Bool
    public let pending = false

    public func withAppearanceReset(_ reset: Bool) -> LocalSetupRemovalResult {
        LocalSetupRemovalResult(status: status, server_revocation_confirmed: server_revocation_confirmed,
            session_was_present: session_was_present, keychain_session_absent: keychain_session_absent,
            generated_setup_absent: generated_setup_absent, removed_files: removed_files,
            retained_files: retained_files, appearance_reset: reset)
    }
}

struct NativeRemovalIntent: Codable {
    let schemaVersion: Int
    let sessionFingerprint: String?
    let profileID: UUID?
    let sessionWasPresent: Bool
    var revoked: Bool
    var files: [NativeOwnedFile]
    var removedCount: Int
    var retained: [NativeOwnedFile]? = nil
}

enum LocalSetupRemoval {
    private static let locks = Set(["connection.lock", "context-optimization.lock", "native-session.lock"])

    static func intent(_ directory: PrivateDirectory) throws -> NativeRemovalIntent? {
        guard let data = try directory.read(NativeOwnership.intentName) else { return nil }
        guard data.count <= 65_536,
              let value = try? JSONDecoder().decode(NativeRemovalIntent.self, from: data),
              value.schemaVersion == 1, value.files.count <= 257,
              value.removedCount >= 0, value.removedCount <= 257,
              Set(value.files.map(\.name)).count == value.files.count else { throw ClientError.unsafeStorage }
        // Validate intent ownership exactly like the manifest; its sole extra
        // file is the manifest itself, never arbitrary user-selected paths.
        let retained = value.retained ?? []
        guard retained.count <= 256, Set(retained.map(\.name)).count == retained.count else { throw ClientError.unsafeStorage }
        for file in value.files + retained {
            _ = try directory.fileURL(file.name)
            guard file.digest.count == 64, file.digest.allSatisfy({ $0.isASCII && ($0.isNumber || "abcdef".contains($0)) }),
                  file.name == NativeOwnership.fileName || NativeOwnership.allowed(file)
            else { throw ClientError.unsafeStorage }
        }
        if let fingerprint = value.sessionFingerprint {
            guard fingerprint.count == 64, fingerprint.allSatisfy({ $0.isASCII && ($0.isNumber || "abcdef".contains($0)) }),
                  value.sessionWasPresent, value.profileID != nil else { throw ClientError.unsafeStorage }
        } else if value.sessionWasPresent { throw ClientError.unsafeStorage }
        return value
    }

    static func save(_ intent: NativeRemovalIntent, directory: PrivateDirectory,
                     synchronize: (() throws -> Void)? = nil) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        try directory.write(try encoder.encode(intent), to: NativeOwnership.intentName,
                            expected: directory.read(NativeOwnership.intentName))
        if let synchronize { try synchronize() } else { try directory.syncMetadata() }
    }

    static func selection(_ directory: PrivateDirectory, record: SessionRecord?, useIntent: Bool = true) throws -> [NativeOwnedFile] {
        if useIntent, let value = try intent(directory) { return value.files }
        var entries = try NativeOwnership.load(directory)
        let retained = try NativeOwnership.load(directory, name: NativeOwnership.retainedFileName)
        let profileData = try directory.read("profile.json")
        let profileDigest = profileData.map(NativeOwnership.digest)
        // A safely readable edit retained during an earlier removal is no
        // longer native setup. Its bytes need not decode as a profile. An
        // active session or an exact current manifest generation stays strict.
        let retainChangedProfile = record == nil && profileDigest.map { digest in
            retained.contains { $0.kind == "profile" && $0.name == "profile.json" && $0.digest != digest }
                && !entries.contains { $0.kind == "profile" && $0.name == "profile.json" && $0.digest == digest }
        } == true
        let profile: ConnectionProfile?
        if retainChangedProfile {
            profile = nil
        } else if let profileData {
            do { profile = try JSONDecoder().decode(ConnectionProfile.self, from: profileData).validated() }
            catch let error as ClientError { throw error }
            catch { throw ClientError.invalidProfile }
        } else {
            profile = nil
        }
        if let record, let profile, profile != record.profile { throw ClientError.identityMismatch }
        // The schema-validated current native selector predates the manifest.
        // Unmanifested legacy launchers are retained instead of inferred from
        // a familiar name/header. Saving them again records exact ownership.
        if let profile, let data = profileData,
           !entries.contains(where: { $0.name == "profile.json" }),
           !retained.contains(where: { $0.name == "profile.json"
               && $0.digest != NativeOwnership.digest(data) }) {
            entries.append(NativeOwnedFile(name: "profile.json", digest: NativeOwnership.digest(data),
                                          profileID: profile.id, kind: "profile"))
        }
        // The entry itself protects possibly shared preferences. Enumerate
        // names without following links or requiring an accessible target.
        let sharedPersonalState = try directory.names().contains("personal-profiles")
        entries = try entries.filter { file in
            if sharedPersonalState && file.kind == "preference" { return false }
            guard let data = try directory.read(file.name) else { return false }
            return NativeOwnership.digest(data) == file.digest
        }
        if let manifest = try directory.read(NativeOwnership.fileName), !entries.isEmpty {
            entries.append(NativeOwnedFile(name: NativeOwnership.fileName, digest: NativeOwnership.digest(manifest),
                                          profileID: profile?.id ?? entries[0].profileID, kind: "manifest"))
        }
        return entries.sorted { $0.name < $1.name }
    }

    static func preview(directory: PrivateDirectory, record: SessionRecord?) throws -> LocalSetupRemovalPreview {
        let pending = try intent(directory)
        if let pending, let record {
            guard try NativeOwnership.sessionFingerprint(record) == pending.sessionFingerprint else {
                throw ClientError.identityMismatch
            }
        }
        let selected = try selection(directory, record: record)
        let selectedNames = Set(selected.map(\.name))
        let retained = try directory.names().filter { name in
            !selectedNames.contains(name) && !locks.contains(name) && name != NativeOwnership.intentName
                && !selected.contains(where: { ".remove-" + NativeOwnership.digest(Data($0.name.utf8)) == name })
        }.count
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var material = Data(try directory.directoryIdentity().utf8)
        material.append(try encoder.encode(selected))
        material.append(Data((try NativeOwnership.sessionFingerprint(record) ?? "absent").utf8))
        if let intentData = try directory.read(NativeOwnership.intentName) { material.append(intentData) }
        return LocalSetupRemovalPreview(preview_token: NativeOwnership.digest(material), generated_files: selected.count,
                                        retained_files: retained, has_session: record != nil, pending: pending != nil)
    }

    static func verify(directory: PrivateDirectory, record: SessionRecord?, allowPending: Bool = false) throws -> LocalSetupRemovalResult {
        let pending = try intent(directory)
        guard pending == nil || allowPending else { throw ClientError.removalPending }
        // Reinspect current setup instead of trusting an exhausted journal.
        // Keep that journal until this succeeds so a partial failure is retryable.
        let selected = try selection(directory, record: record, useIntent: false)
        guard record == nil, selected.isEmpty else { throw ClientError.removalPending }
        let retained = try directory.names().filter { !locks.contains($0) && $0 != NativeOwnership.intentName }.count
        return LocalSetupRemovalResult(status: retained == 0 ? "complete" : "complete_with_retained_files",
            server_revocation_confirmed: false, session_was_present: false, keychain_session_absent: true,
            generated_setup_absent: true, removed_files: 0,
            retained_files: retained, appearance_reset: false)
    }
}

extension SessionController {
    public func previewLocalSetupRemoval() async throws -> LocalSetupRemovalPreview {
        let lock = try await lockConnection(allowAbsent: true); defer { lock.unlock() }
        if !(try directory.verifiedExists()) {
            guard try store.load() == nil else { throw ClientError.storageUnavailable }
            return LocalSetupRemovalPreview(preview_token: NativeOwnership.digest(Data((directory.root.path + ":absent").utf8)),
                generated_files: 0, retained_files: 0, has_session: false, pending: false)
        }
        return try LocalSetupRemoval.preview(directory: directory, record: store.load())
    }

    public func verifyLocalSetupRemoval() async throws -> LocalSetupRemovalResult {
        let lock = try await lockConnection(allowAbsent: true); defer { lock.unlock() }
        if !(try directory.verifiedExists()) {
            guard try store.load() == nil else { throw ClientError.storageUnavailable }
            return LocalSetupRemovalResult(status: "complete", server_revocation_confirmed: false,
                session_was_present: false, keychain_session_absent: true, generated_setup_absent: true,
                removed_files: 0, retained_files: 0, appearance_reset: false)
        }
        return try LocalSetupRemoval.verify(directory: directory, record: store.load())
    }

    public func applyLocalSetupRemoval(previewToken: String) async throws -> LocalSetupRemovalResult {
        // GUI releases its own lease first. CLI cannot close a different app's
        // lease, and legacy clients must be closed by their owner explicitly.
        try removalSafety()
        let lock = try await lockConnection(allowAbsent: true); defer { lock.unlock() }
        try removalSafety()
        if !(try directory.verifiedExists()) {
            guard try store.load() == nil else { throw ClientError.storageUnavailable }
            guard previewToken == NativeOwnership.digest(Data((directory.root.path + ":absent").utf8)) else {
                throw ClientError.configurationChanged
            }
            return LocalSetupRemovalResult(status: "complete", server_revocation_confirmed: false,
                session_was_present: false, keychain_session_absent: true, generated_setup_absent: true,
                removed_files: 0, retained_files: 0, appearance_reset: false)
        }
        let preferenceLock = try await directory.lock(name: "context-optimization.lock")
        defer { preferenceLock.unlock() }
        var saved = try store.load()
        let current = try LocalSetupRemoval.preview(directory: directory, record: saved)
        guard previewToken == current.preview_token else { throw ClientError.configurationChanged }
        var intent = try LocalSetupRemoval.intent(directory) ?? NativeRemovalIntent(schemaVersion: 1,
            sessionFingerprint: try NativeOwnership.sessionFingerprint(saved), profileID: saved?.profile.id,
            sessionWasPresent: saved != nil, revoked: false,
            files: try LocalSetupRemoval.selection(directory, record: saved), removedCount: 0)
        try LocalSetupRemoval.save(intent, directory: directory, synchronize: removalJournalSync)
        if var record = saved {
            guard try NativeOwnership.sessionFingerprint(record) == intent.sessionFingerprint else {
                throw ClientError.identityMismatch
            }
            if !intent.revoked {
                record.state = .revocationPending
                try store.save(record)
                let reply: GatewayReply
                do {
                    let body = try JSONSerialization.data(withJSONObject: ["credential": record.refreshToken])
                    reply = try await transport.request(profile: record.profile, path: "/v1/auth/logout",
                                                        body: body, accessToken: nil)
                    struct Revoked: Decodable { let revoked: Bool }
                    guard reply.status == 200, try reply.decode(Revoked.self).revoked else { throw ClientError.logoutPending }
                } catch { throw ClientError.logoutPending }
                intent.revoked = true
                try LocalSetupRemoval.save(intent, directory: directory, synchronize: removalJournalSync)
            }
            // Exact compare immediately before deleting the shared slot.
            guard try NativeOwnership.sessionFingerprint(store.load()) == intent.sessionFingerprint else {
                throw ClientError.identityMismatch
            }
            try store.delete()
            saved = nil
        } else if intent.sessionWasPresent && !intent.revoked {
            throw ClientError.removalPending
        }
        while !intent.files.isEmpty {
            // The ownership manifest stays until all actual setup entries are
            // settled; a safe edited original is retained rather than retried
            // forever with its previous digest. Ambiguous stages still refuse.
            let index = intent.files.firstIndex { $0.name != NativeOwnership.fileName } ?? 0
            let file = intent.files[index]
            let stage = ".remove-" + NativeOwnership.digest(Data(file.name.utf8))
            if let current = try directory.read(file.name), NativeOwnership.digest(current) != file.digest {
                guard try directory.read(stage) == nil else { throw ClientError.removalRecoveryRequired }
                if file.name != NativeOwnership.fileName {
                    var retained = intent.retained ?? []
                    retained.removeAll { $0.name == file.name }; retained.append(file)
                    intent.retained = retained
                }
                intent.files.remove(at: index)
                try LocalSetupRemoval.save(intent, directory: directory, synchronize: removalJournalSync)
                continue
            }
            if try directory.removeExpected(file.name, digest: file.digest) { intent.removedCount += 1 }
            intent.files.remove(at: index)
            try LocalSetupRemoval.save(intent, directory: directory, synchronize: removalJournalSync)
        }
        let retainedProfiles = (intent.retained ?? []).filter { $0.kind == "profile" }
        if !retainedProfiles.isEmpty {
            // Content-free old generation metadata prevents deliberately kept
            // edited selectors from later being adopted as legacy setup. A
            // newly saved manifest-owned generation is still removable.
            try NativeOwnership.record(retainedProfiles, directory: directory, name: NativeOwnership.retainedFileName)
            try removalJournalSync()
        }
        guard try store.load() == nil else { throw ClientError.identityMismatch }
        let checked = try LocalSetupRemoval.verify(directory: directory, record: nil, allowPending: true)
        if let journal = try directory.read(NativeOwnership.intentName) {
            try directory.removeExpected(NativeOwnership.intentName, digest: NativeOwnership.digest(journal))
        }
        return LocalSetupRemovalResult(status: checked.status, server_revocation_confirmed: intent.revoked,
            session_was_present: intent.sessionWasPresent, keychain_session_absent: true,
            generated_setup_absent: checked.generated_setup_absent, removed_files: intent.removedCount,
            retained_files: checked.retained_files, appearance_reset: false)
    }
}
