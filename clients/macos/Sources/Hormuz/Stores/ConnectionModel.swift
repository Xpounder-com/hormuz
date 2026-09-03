import AppKit
import Foundation
import HormuzClientCore
import Observation

@MainActor @Observable final class ConnectionModel {
    var gateway = ""
    var organization = ""
    var issuer = ""
    var client = AIClient.codex
    var model = ""
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
    var showingPreview = false
    var message: String?
    private var controller: SessionController?
    private var directory: PrivateDirectory?
    private var operation: Task<Void, Never>?
    private var didRestore = false

    var statusLabel: String {
        if awaitingBrowser { return "Waiting for browser sign-in" }
        if sessionState == .revocationPending { return "Sign-out pending" }
        if sessionState == .refreshPending { return "Sign-in needs attention" }
        if let expiresAt, expiresAt <= Date(), hasSession { return "Session expired" }
        if dashboard != nil { return "Gateway verified" }
        return hasSession ? "Signed in · not yet verified" : "Not connected"
    }

    func restore() async {
        guard !didRestore else { return }
        didRestore = true
        do {
            let directory = try PrivateDirectory()
            self.directory = directory
            controller = SessionController(directory: directory)
            try await syncStatus()
            if hasSession { refresh() }
        } catch { message = ClientError.message(for: error) }
    }

    func signIn() {
        run {
            guard let controller = self.controller else { throw ClientError.storageUnavailable }
            let profile = try ConnectionProfile(gateway: self.gateway, organization: self.organization,
                issuer: self.issuer.isEmpty ? nil : self.issuer, client: self.client, model: self.model,
                allowLoopbackHTTP: self.allowLoopbackHTTP)
            self.dashboard = nil
            self.connector = nil
            self.connectorSaved = false
            try await controller.signIn(profile: profile) { url in
                await MainActor.run {
                    self.loginURL = url
                    self.awaitingBrowser = true
                    if !NSWorkspace.shared.open(url) {
                        self.message = "The browser could not open. Use Open sign-in page to continue."
                    }
                }
            }
            self.awaitingBrowser = false
            self.loginURL = nil
            try await self.syncStatus()
            self.dashboard = try await controller.dashboard(profileID: profile.id)
        }
    }

    func cancelSignIn() { operation?.cancel() }

    func reopenBrowser() { if let loginURL { NSWorkspace.shared.open(loginURL) } }

    func refresh() {
        run {
            guard let controller = self.controller, let profile = self.profile else { throw ClientError.loginRequired }
            // A previous success must not remain a green indicator after failure.
            self.dashboard = nil
            self.dashboard = try await controller.dashboard(profileID: profile.id)
            try await self.syncStatus()
        }
    }

    func signOut() {
        run {
            guard let controller = self.controller else { throw ClientError.storageUnavailable }
            self.dashboard = nil
            try await controller.signOut()
            try await self.syncStatus()
            self.message = "Session revoked. Local credentials were removed. The saved launcher will require a new sign-in."
        }
    }

    func previewConnector() {
        do {
            guard let profile, let directory, hasSession, sessionState == .active,
                  let executable = Bundle.main.executableURL else { throw ClientError.loginRequired }
            connector = try ConnectorPlan.preview(profile: profile, directory: directory, helper: executable)
            showingPreview = true
        } catch { message = ClientError.message(for: error) }
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
            client = profile.client
            model = profile.model
            allowLoopbackHTTP = profile.allowLoopbackHTTP
        }
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
