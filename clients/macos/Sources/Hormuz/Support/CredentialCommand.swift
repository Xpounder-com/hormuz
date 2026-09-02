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
        guard arguments.count == 5 || (arguments.count == 6 && arguments[5] == "--force-refresh"),
              arguments[0] == "credential", arguments[1] == "--profile",
              arguments[3] == "--state-directory", arguments[4].hasPrefix("/"),
              let profileID = UUID(uuidString: arguments[2]) else {
            fail(ClientError.invalidArguments)
        }
        Task {
            do {
                let controller = SessionController(directory: try PrivateDirectory(root: URL(fileURLWithPath: arguments[4])))
                let token = try await controller.accessCredential(profileID: profileID, forceRefresh: arguments.count == 6)
                // Explicit machine credential channel, never mirrored to logs.
                FileHandle.standardOutput.write(Data((token + "\n").utf8))
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
