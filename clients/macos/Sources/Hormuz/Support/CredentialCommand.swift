import Darwin
import Foundation
import HormuzClientCore

enum CredentialCommand {
    static func run(arguments: [String]) -> Never {
        if arguments == ["--version"] {
            let packagedVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            print("Hormuz Mac " + (packagedVersion ?? "0.1.0-local"))
            exit(0)
        }
        let operation: String
        let profileArgument: String
        let stateDirectory: String
        let forceRefresh: Bool
        if arguments.count == 5 || (arguments.count == 6 && arguments[5] == "--force-refresh"),
           arguments[0] == "credential", arguments[1] == "--profile",
           arguments[3] == "--state-directory" {
            operation = "credential"
            profileArgument = arguments[2]
            stateDirectory = arguments[4]
            forceRefresh = arguments.count == 6
        } else if arguments.count == 6, arguments[0] == "pilot-evidence",
                  arguments[2] == "--profile", arguments[4] == "--state-directory" {
            operation = arguments[1]
            profileArgument = arguments[3]
            stateDirectory = arguments[5]
            forceRefresh = false
        } else {
            fail(ClientError.invalidArguments)
        }
        let allowedPilotOperations = [
            "verify-session", "refresh", "server-revoke", "verify-denied",
            "sign-out", "session-absent", "reliability",
        ]
        guard stateDirectory.hasPrefix("/"), let profileID = UUID(uuidString: profileArgument),
              operation == "credential" || allowedPilotOperations.contains(operation) else {
            fail(ClientError.invalidArguments)
        }
        Task {
            do {
                let controller = SessionController(directory: try PrivateDirectory(root: URL(fileURLWithPath: stateDirectory)))
                switch operation {
                case "credential":
                    let token = try await controller.accessCredential(profileID: profileID, forceRefresh: forceRefresh)
                    // Explicit machine credential channel, never mirrored to logs.
                    FileHandle.standardOutput.write(Data((token + "\n").utf8))
                case "verify-session":
                    try await controller.verifySession(profileID: profileID)
                    print("pilot_evidence=active_session_verified")
                case "reliability":
                    let snapshot = try await controller.reliability(profileID: profileID)
                    let encoder = JSONEncoder()
                    encoder.outputFormatting = [.sortedKeys]
                    FileHandle.standardOutput.write(try encoder.encode(snapshot))
                    FileHandle.standardOutput.write(Data("\n".utf8))
                case "refresh":
                    try await controller.refreshSession(profileID: profileID)
                    print("pilot_evidence=session_refresh_verified")
                case "server-revoke":
                    try await controller.revokeServerSession(profileID: profileID)
                    print("pilot_evidence=server_revocation_completed")
                case "verify-denied":
                    try await controller.verifyServerRevocation(profileID: profileID)
                    print("pilot_evidence=server_revocation_denial_verified")
                case "sign-out":
                    try await controller.signOut()
                    print("pilot_evidence=local_session_removed")
                case "session-absent":
                    try await controller.verifySessionAbsent(profileID: profileID)
                    print("pilot_evidence=session_absence_verified")
                default:
                    fail(ClientError.invalidArguments)
                }
                exit(0)
            } catch { fail(error) }
        }
        dispatchMain()
    }

    private static func fail(_ error: Error) -> Never {
        FileHandle.standardError.write(Data((ClientError.message(for: error) + "\n").utf8))
        exit(1)
    }
}
