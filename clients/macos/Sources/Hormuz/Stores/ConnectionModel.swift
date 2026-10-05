import AppKit
import Foundation
import HormuzClientCore
import Observation

@MainActor @Observable final class ConnectionModel {
    var gateway = ""
    var organization = ""
    var issuer = ""
    var setup = GatewaySetup.openAIPilot
    var client = AIClient.codex
    var model = "openai-primary"
    var allowLoopbackHTTP = false
    private(set) var profile: ConnectionProfile?
    private(set) var hasSession = false
    private(set) var sessionState: SessionState?
    private(set) var expiresAt: Date?
    private(set) var dashboard: Dashboard?
    private(set) var isBusy = false
    private(set) var awaitingBrowser = false
    private(set) var loginURL: URL?
    private(set) var connector: ConnectorPlan?
    private(set) var connectorSaved = false
    private(set) var contextOptimizationEnabled = false
    private(set) var contextOptimizationStatus = ContextOptimizationStatus.off
    var showingPreview = false
    var message: String?
    private var controller: SessionController?
    private var directory: PrivateDirectory?
    private var relayOwner: RelayOwner?
    private var operation: Task<Void, Never>?
    private var didRestore = false
    private let desktopOrigin = Bundle.main.object(forInfoDictionaryKey: "HormuzDesktopOrigin") as? String

    init() {
        client = Self.installedClient()
    }

    private static func installedClient() -> AIClient {
        let paths = (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map(String.init)
            + ["/opt/homebrew/bin", "/usr/local/bin", FileManager.default.homeDirectoryForCurrentUser.path + "/.local/bin"]
        let codexFound = paths.contains { FileManager.default.isExecutableFile(atPath: $0 + "/codex") }
        let claudeFound = paths.contains { FileManager.default.isExecutableFile(atPath: $0 + "/claude") }
        return !codexFound && claudeFound ? .claudeCode : .codex
    }

    var desktopSignInAvailable: Bool {
        guard let desktopOrigin else { return false }
        return (try? ConnectionProfile.normalizeGateway(desktopOrigin,
            allowLoopbackHTTP: desktopAllowsLoopback)) != nil
    }

    private var desktopAllowsLoopback: Bool {
        Bundle.main.bundleIdentifier == "com.hormuz.mac.local"
    }

    var statusLabel: String {
        if awaitingBrowser { return "Waiting for browser sign-in" }
        if sessionState == .revocationPending { return "Sign-out pending" }
        if sessionState == .refreshPending { return "Sign-in needs attention" }
        if let expiresAt, expiresAt <= Date(), hasSession { return "Session expired" }
        if dashboard != nil { return "Gateway verified" }
        return hasSession ? "Signed in · not yet verified" : "Not connected"
    }

    var contextOptimizationStatusLabel: String { contextOptimizationStatus.label }

    func restore() async {
        guard !didRestore else { return }
        didRestore = true
        do {
            let directory = try PrivateDirectory()
            self.directory = directory
            relayOwner = try RelayOwner()
            controller = SessionController(directory: directory)
            try await syncStatus()
            if hasSession { refresh() }
        } catch { message = ClientError.message(for: error) }
    }

    func signIn() {
        setup = .custom
        run { try await self.performSignIn() }
    }

    func joinTeam() {
        setup = .custom
        run { try await self.performSignIn(joinTeam: true) }
    }

    func signInDesktop() {
        run { try await self.performDesktopSignIn() }
    }

    func joinTeamDesktop() {
        run { try await self.performDesktopSignIn(joinTeam: true) }
    }

    private func performDesktopSignIn(joinTeam: Bool = false) async throws {
        guard let configuredOrigin = desktopOrigin, desktopSignInAvailable,
              let controller else { throw ClientError.desktopUnavailable }
        let origin = try ConnectionProfile.normalizeGateway(configuredOrigin,
            allowLoopbackHTTP: desktopAllowsLoopback)
        dashboard = nil
        connector = nil
        connectorSaved = false
        // Manual profile restoration must not override hosted client detection.
        // Keep an already approved desktop client when reconnecting to its origin.
        let chosenClient: AIClient
        if let profile, profile.desktopManaged, profile.gateway == origin {
            chosenClient = profile.client
        } else {
            chosenClient = Self.installedClient()
        }
        try await controller.signInDesktop(origin: origin, client: chosenClient,
            allowLoopbackHTTP: desktopAllowsLoopback, joinTeam: joinTeam) { url in
            await MainActor.run {
                self.loginURL = url
                self.awaitingBrowser = true
                if !NSWorkspace.shared.open(url) {
                    self.message = "The browser could not open. Use Open sign-in page to continue."
                }
            }
        }
        awaitingBrowser = false
        loginURL = nil
        try await syncStatus()
        guard let profile else { throw ClientError.loginRequired }
        dashboard = try await controller.dashboard(profileID: profile.id)
        try await prepareLauncher()
    }

    func reconnect() {
        run {
            guard let controller = self.controller else { throw ClientError.storageUnavailable }
            let desktopManaged = self.profile?.desktopManaged == true
            self.dashboard = nil
            // Retire the previous session first. Failed revocation must not create
            // a second session or silently discard the Keychain retry record.
            try await controller.signOut()
            try await self.syncStatus()
            if desktopManaged { try await self.performDesktopSignIn() }
            else { try await self.performSignIn() }
        }
    }

    private func performSignIn(joinTeam: Bool = false) async throws {
        guard let controller else { throw ClientError.storageUnavailable }
        let profile = try ConnectionProfile(gateway: gateway, organization: organization,
            issuer: issuer.isEmpty ? nil : issuer, client: client, model: model,
            allowLoopbackHTTP: allowLoopbackHTTP, setup: setup)
        dashboard = nil
        connector = nil
        connectorSaved = false
        try await controller.signIn(profile: profile, joinTeam: joinTeam) { url in
            await MainActor.run {
                self.loginURL = url
                self.awaitingBrowser = true
                if !NSWorkspace.shared.open(url) {
                    self.message = "The browser could not open. Use Open sign-in page to continue."
                }
            }
        }
        awaitingBrowser = false
        loginURL = nil
        try await syncStatus()
        dashboard = try await controller.dashboard(profileID: profile.id)
    }

    func cancelSignIn() { operation?.cancel() }

    func selectSetup(_ selected: GatewaySetup) {
        guard !isBusy else { return }
        setup = selected
        if selected == .openAIPilot {
            client = .codex
            if !["openai-primary", "openai-secondary"].contains(model) {
                model = "openai-primary"
            }
            allowLoopbackHTTP = false
        }
    }

    func reopenBrowser() { if let loginURL { NSWorkspace.shared.open(loginURL) } }

    func refresh() {
        run {
            guard let controller = self.controller, let profile = self.profile else { throw ClientError.loginRequired }
            // A previous success must not remain a green indicator after failure.
            self.dashboard = nil
            self.dashboard = try await controller.dashboard(profileID: profile.id)
            try await self.syncStatus()
            // A saved launcher holds this app lifetime's lease. Rebind it after
            // restart only once the restored session is gateway-verified. First
            // manual setup still requires its normal preview/save action.
            let savedLauncher = profile.client.rawValue + "-" + profile.key + ".command"
            let wasSaved = try self.directory?.read(savedLauncher) != nil
            if profile.desktopManaged || wasSaved {
                try await self.prepareLauncher()
            }
        }
    }

    func signOut() {
        run {
            guard let controller = self.controller else { throw ClientError.storageUnavailable }
            self.dashboard = nil
            try await controller.signOut()
            try await self.syncStatus()
            self.connector = nil
            self.connectorSaved = false
            self.message = "Session revoked. Local credentials were removed. The saved launcher will require a new sign-in."
        }
    }

    @discardableResult
    func previewConnector(presentSheet: Bool = true) -> Bool {
        do {
            guard let profile, let directory, let relayOwner, hasSession, sessionState == .active,
                  let executable = Bundle.main.executableURL else { throw ClientError.loginRequired }
            connector = try ConnectorPlan.preview(profile: profile, directory: directory, helper: executable,
                ownerSocket: relayOwner.socketURL)
            showingPreview = presentSheet
            return true
        } catch {
            message = ClientError.message(for: error)
            return false
        }
    }

    func saveConnector() {
        run {
            guard let connector = self.connector, let directory = self.directory else { throw ClientError.storageUnavailable }
            try await connector.apply(in: directory)
            self.connectorSaved = true
            self.showingPreview = false
            self.message = "Connector saved. Copy its command and run it from your project directory. Existing client settings are unchanged."
        }
    }

    func copyCommand() {
        guard connectorSaved, let connector else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(connector.command, forType: .string)
        message = "Launcher command copied. No credentials were copied."
    }

    func openClient() {
        guard connectorSaved, let connector, hasSession, dashboard != nil,
              sessionState == .active, expiresAt.map({ $0 > Date() }) == true else { return }
        if !NSWorkspace.shared.open(connector.launcher) {
            message = "Terminal could not open the Hormuz launcher. Review the client setup in Advanced."
        }
    }

    private func prepareLauncher() async throws {
        guard let profile, let directory, let relayOwner,
              let executable = Bundle.main.executableURL else { throw ClientError.storageUnavailable }
        let plan = try ConnectorPlan.preview(profile: profile, directory: directory, helper: executable,
            ownerSocket: relayOwner.socketURL)
        try await plan.apply(in: directory)
        connector = plan
        connectorSaved = true
    }

    func stopLaunchedClients() { relayOwner?.stop() }

    func setContextOptimization(enabled: Bool) {
        run {
            guard let profile = self.profile, let directory = self.directory else {
                throw ClientError.loginRequired
            }
            let preference = try await ContextOptimizationSettings.save(
                enabled: enabled, profile: profile, directory: directory
            )
            self.contextOptimizationEnabled = preference.enabled
            self.contextOptimizationStatus = await self.contextStatus(
                preference: preference, profile: profile, directory: directory
            )
            self.message = preference.enabled
                ? "Context optimization is on. Its readiness applies to the next request; new chats have the most stable cache behavior."
                : "Context optimization is off. Requests pass through the local helper unchanged."
        }
    }

    private func syncStatus() async throws {
        guard let controller else { throw ClientError.storageUnavailable }
        let status = try await controller.status()
        profile = status.profile
        hasSession = status.hasSession
        sessionState = status.sessionState
        expiresAt = status.expiresAt
        if let profile {
            gateway = profile.gateway
            organization = profile.organization
            issuer = profile.issuer ?? ""
            setup = profile.setup
            client = profile.client
            model = profile.model
            allowLoopbackHTTP = profile.allowLoopbackHTTP
            if let directory {
                do {
                    let preference = try ContextOptimizationSettings.load(
                        profile: profile, directory: directory
                    )
                    contextOptimizationEnabled = preference.enabled
                    contextOptimizationStatus = await contextStatus(
                        preference: preference, profile: profile, directory: directory
                    )
                } catch {
                    contextOptimizationEnabled = false
                    contextOptimizationStatus = .settingsInvalid
                }
            }
        } else {
            contextOptimizationEnabled = false
            contextOptimizationStatus = .off
        }
    }

    private func contextStatus(
        preference: ContextOptimizationPreference,
        profile: ConnectionProfile,
        directory: PrivateDirectory
    ) async -> ContextOptimizationStatus {
        guard preference.enabled else { return .off }
        guard let executable = Bundle.main.executableURL else {
            return .resourcesUnavailable
        }
        let helper = executable.deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Resources/ContextHelper/hormuz-context")
        return await ContextOptimizationSettings.probeStatus(
            profile: profile, directory: directory, helper: helper
        )
    }

    private func run(_ action: @escaping @MainActor () async throws -> Void) {
        guard !isBusy else { return }
        isBusy = true
        message = nil
        operation = Task {
            do { try await action() }
            catch { message = ClientError.message(for: error) }
            awaitingBrowser = false
            loginURL = nil
            // Read persisted refresh/revocation state after a failed operation.
            try? await syncStatus()
            isBusy = false
            operation = nil
        }
    }
}
