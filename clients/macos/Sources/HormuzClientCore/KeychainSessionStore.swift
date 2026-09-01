import Foundation
import Security

public protocol SessionStore: Sendable {
    func load() throws -> SessionRecord?
    func save(_ record: SessionRecord) throws
    func delete() throws
}

/// A single active local connection. The app and its command-line helper are the
/// same executable, avoiding a second executable's Keychain access requirements.
/// No fallback to preferences, environment variables, or plaintext files.
/// This local development build uses the macOS file Keychain through SecItem.
/// It does not claim Data Protection, device binding, or screen-lock semantics.
public final class KeychainSessionStore: SessionStore {
    private let service: String
    private let account = "active-connection-v1"

    public init(service: String = "com.hormuz.mac.session.v1") { self.service = service }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: account,
         kSecAttrSynchronizable as String: false]
    }

    public func load() throws -> SessionRecord? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &value)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = value as? Data, data.count < 32_768 else {
            throw ClientError.secureStoreUnavailable
        }
        do { return try JSONDecoder().decode(SessionRecord.self, from: data) }
        catch { throw ClientError.secureStoreUnavailable }
    }

    public func save(_ record: SessionRecord) throws {
        let data: Data
        do { data = try JSONEncoder().encode(record.validated(for: record.profile)) }
        catch { throw ClientError.secureStoreUnavailable }
        let updated = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else { throw ClientError.secureStoreUnavailable }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrLabel as String] = "Hormuz team session"
        // kSecAttrAccessible is not supported by the file Keychain. Do not add
        // a decorative ThisDeviceOnly value: a production Data Protection
        // backend requires explicit provisioning and its own validation.
        guard SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess else { throw ClientError.secureStoreUnavailable }
    }

    public func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw ClientError.secureStoreUnavailable }
    }
}
