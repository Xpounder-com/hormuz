import CoreFoundation
import Foundation

public enum ContextOptimizationStatus: String, Sendable {
    case off
    case ready
    case resourcesUnavailable = "resources_unavailable"
    case unsupportedClient = "unsupported_client"
    case unsupportedHistory = "unsupported_history"
    case gatewayIncompatible = "gateway_incompatible"
    case settingsInvalid = "settings_invalid"

    public var label: String {
        switch self {
        case .off: "Off"
        case .ready: "Ready for eligible tool results"
        case .resourcesUnavailable: "Tokenizer resources need setup"
        case .unsupportedClient: "This client version is not supported"
        case .unsupportedHistory: "Current history will pass through unchanged"
        case .gatewayIncompatible: "Gateway upgrade required"
        case .settingsInvalid: "Local setting could not be verified"
        }
    }
}

public struct ContextOptimizationPreference: Equatable, Sendable {
    public let enabled: Bool
    public init(enabled: Bool = false) { self.enabled = enabled }
}

/// The Python relay and native app share this small, non-secret file. Missing
/// means Off. Invalid values are never repaired implicitly or treated as On.
public enum ContextOptimizationSettings {
    public static func fileName(profile: ConnectionProfile) -> String {
        "context-optimization-" + profile.key + ".json"
    }

    public static func load(
        profile: ConnectionProfile,
        directory: PrivateDirectory
    ) throws -> ContextOptimizationPreference {
        guard let data = try directory.read(fileName(profile: profile)) else {
            return ContextOptimizationPreference()
        }
        guard data.count <= 4_096 else { throw ClientError.contextSettingsInvalid }
        do {
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  Set(object.keys) == Set(["schema_version", "enabled"]),
                  let schema = object["schema_version"] as? NSNumber,
                  CFGetTypeID(schema) != CFBooleanGetTypeID(),
                  !CFNumberIsFloatType(schema),
                  schema.intValue == 1,
                  let enabled = object["enabled"] as? NSNumber,
                  CFGetTypeID(enabled) == CFBooleanGetTypeID()
            else { throw ClientError.contextSettingsInvalid }
            let canonical = try JSONSerialization.data(
                withJSONObject: object,
                options: [.sortedKeys]
            )
            guard canonical == data else { throw ClientError.contextSettingsInvalid }
            return ContextOptimizationPreference(enabled: enabled.boolValue)
        } catch let error as ClientError {
            throw error
        } catch {
            throw ClientError.contextSettingsInvalid
        }
    }

    @discardableResult
    public static func save(
        enabled: Bool,
        profile: ConnectionProfile,
        directory: PrivateDirectory
    ) async throws -> ContextOptimizationPreference {
        let lock = try await directory.lock(name: "context-optimization.lock")
        defer { lock.unlock() }
        let name = fileName(profile: profile)
        let previous = try directory.read(name)
        if previous != nil { _ = try load(profile: profile, directory: directory) }
        let body = try JSONSerialization.data(
            withJSONObject: ["schema_version": 1, "enabled": enabled],
            options: [.sortedKeys]
        )
        try directory.write(body, to: name, expected: previous)
        let saved = try load(profile: profile, directory: directory)
        guard saved.enabled == enabled else { throw ClientError.storageUnavailable }
        return saved
    }

    public static func probeStatus(
        profile: ConnectionProfile,
        directory: PrivateDirectory,
        helper: URL
    ) async -> ContextOptimizationStatus {
        guard helper.isFileURL, helper.path.hasPrefix("/"),
              !helper.path.unicodeScalars.contains(where: {
                  CharacterSet.controlCharacters.contains($0)
              })
        else { return .resourcesUnavailable }
        let profileKey = profile.key
        let statePath = directory.root.path
        return await Task.detached(priority: .utility) {
            let process = Process()
            let output = Pipe()
            process.executableURL = helper
            process.arguments = [
                "context", "status", "--profile", profileKey,
                "--state-directory", statePath, "--readiness-only",
            ]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard data.count <= 256,
                      let line = String(data: data, encoding: .utf8),
                      line.hasSuffix("\n"),
                      line.hasPrefix("context_optimization setting="),
                      let statusField = line.split(separator: " ").last,
                      statusField.hasPrefix("status="),
                      let status = ContextOptimizationStatus(
                          rawValue: String(statusField.dropFirst("status=".count))
                              .trimmingCharacters(in: .whitespacesAndNewlines)
                      )
                else { return .settingsInvalid }
                return status
            } catch {
                return .resourcesUnavailable
            }
        }.value
    }
}
