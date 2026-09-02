import Darwin
import Foundation
import HormuzClientCore

final class FixtureSessions: SessionStore, @unchecked Sendable {
    private let lock = NSLock()
    private var record: SessionRecord?
    func load() throws -> SessionRecord? { lock.lock(); defer { lock.unlock() }; return record }
    func save(_ value: SessionRecord) throws { lock.lock(); defer { lock.unlock() }; record = value }
    func delete() throws { lock.lock(); defer { lock.unlock() }; record = nil }
}

enum ProbeFailure: Error { case failed }
func require(_ value: Bool) throws { if !value { throw ProbeFailure.failed } }

/// A simulated browser, separate from the native transport, with its own cookies.
func fixtureBrowser(_ login: URL) async throws {
    let session = URLSession(configuration: .ephemeral)
    defer { session.invalidateAndCancel() }
    let (page, response) = try await session.data(from: login)
    try require((response as? HTTPURLResponse)?.statusCode == 200)
    let html = String(decoding: page, as: UTF8.self)
    let expression = try NSRegularExpression(pattern: "href=\"([^\"]+)\"")
    guard let match = expression.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
          let range = Range(match.range(at: 1), in: html),
          let authorization = URL(string: String(html[range]).replacingOccurrences(of: "&amp;", with: "&")) else {
        throw ProbeFailure.failed
    }
    try require(authorization.scheme == "http" && authorization.host == "127.0.0.1")
    var request = URLRequest(url: authorization)
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    let (data, authResponse) = try await session.data(for: request)
    try require((authResponse as? HTTPURLResponse)?.statusCode == 200)
    guard let fields = try JSONSerialization.jsonObject(with: data) as? [String: String],
          let host = login.host, let port = login.port else { throw ProbeFailure.failed }
    var form = URLComponents()
    form.queryItems = fields.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
    var callback = URLRequest(url: URL(string: "http://" + host + ":" + String(port) + "/v1/auth/callback")!)
    callback.httpMethod = "POST"
    callback.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
    callback.httpBody = Data((form.percentEncodedQuery ?? "").utf8)
    let (_, completion) = try await session.data(for: callback)
    try require((completion as? HTTPURLResponse)?.statusCode == 200)
}

func verify(gateway: String, idp: String, root: String) async throws {
    let directory = try PrivateDirectory(root: URL(fileURLWithPath: root))
    let store = FixtureSessions()
    let transport = HTTPGatewayTransport()
    let controller = SessionController(directory: directory, store: store, transport: transport)
    let profile = try ConnectionProfile(gateway: gateway, organization: "org-a", client: .codex, model: "safe-openai", allowLoopbackHTTP: true)
    try require(URL(string: profile.gateway)?.host == "127.0.0.1")
    try await controller.signIn(profile: profile) { try await fixtureBrowser($0) }
    let initial = try await controller.dashboard(profileID: profile.id)
    try require(initial.identity.actorId == "alice" && initial.identity.organizationId == "org-a" && initial.usage.requests == 0)
    let token = try await controller.accessCredential(profileID: profile.id)
    let request = try JSONSerialization.data(withJSONObject: ["model": "safe-openai", "input": "Synthetic local fixture request only."])
    let result = try await transport.request(profile: profile, path: "/v1/responses", body: request, accessToken: token)
    try require(result.status == 200)
    let dashboard = try await controller.dashboard(profileID: profile.id)
    try require(dashboard.usage.requests == 1 && dashboard.usage.inputTokens == 120 && dashboard.usage.outputTokens == 30)
    let wrongClient = try JSONSerialization.data(withJSONObject: ["model": "safe-claude", "messages": [["role": "user", "content": "fixture"]], "max_tokens": 16])
    let denied = try await transport.request(profile: profile, path: "/v1/messages", body: wrongClient, accessToken: token)
    try require(denied.status == 403)

    // Client-supplied organization IDs cannot broaden server scope.
    var scoped = URLRequest(url: URL(string: gateway + "/v1/gateway/usage?organization_id=org-b&actor_id=bob")!)
    scoped.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
    scoped.setValue("org-b", forHTTPHeaderField: "X-Hormuz-Organization")
    let browserless = URLSession(configuration: .ephemeral)
    defer { browserless.invalidateAndCancel() }
    let (scopedData, scopedReply) = try await browserless.data(for: scoped)
    try require((scopedReply as? HTTPURLResponse)?.statusCode == 200)
    let scopedUsage = try GatewayJSON.decoder().decode(PersonalUsage.self, from: scopedData)
    try require(scopedUsage.inputTokens == 120 && scopedUsage.outputTokens == 30)

    let rotated = try await controller.accessCredential(profileID: profile.id, forceRefresh: true)
    try require(rotated != token)
    let old = try await transport.request(profile: profile, path: "/v1/gateway/whoami", body: nil, accessToken: token)
    try require(old.status == 401)
    let new = try await transport.request(profile: profile, path: "/v1/gateway/whoami", body: nil, accessToken: rotated)
    try require(new.status == 200)
    let plan = try ConnectorPlan.preview(profile: profile, directory: directory, helper: URL(fileURLWithPath: "/Applications/Hormuz.app/Contents/MacOS/Hormuz"))
    try await plan.apply(in: directory)
    try require(FileManager.default.isExecutableFile(atPath: plan.launcher.path))
    try await controller.signOut()
    let revoked = try await transport.request(profile: profile, path: "/v1/gateway/whoami", body: nil, accessToken: rotated)
    try require(revoked.status == 401 && store.load() == nil)

    // Bob is selectable only in this fake IdP. A second tenant sees no Alice usage.
    try require(URL(string: idp)?.scheme == "http" && URL(string: idp)?.host == "127.0.0.1")
    var selectBob = URLRequest(url: URL(string: idp + "/fixture/subject/bob")!)
    selectBob.httpMethod = "POST"
    selectBob.httpBody = Data("{}".utf8)
    let (_, selected) = try await browserless.data(for: selectBob)
    try require((selected as? HTTPURLResponse)?.statusCode == 200)
    let bob = try ConnectionProfile(gateway: gateway, organization: "org-b", client: .codex, model: "safe-openai", allowLoopbackHTTP: true)
    try await controller.signIn(profile: bob) { try await fixtureBrowser($0) }
    let other = try await controller.dashboard(profileID: bob.id)
    try require(other.identity.actorId == "bob" && other.identity.organizationId == "org-b" && other.usage.requests == 0)
    try await controller.signOut()

    let transportProfile = try ConnectionProfile(gateway: idp, organization: "fixture", client: .codex, model: "fixture", allowLoopbackHTTP: true)
    do {
        _ = try await transport.request(profile: transportProfile, path: "/v1/fixture/redirect", body: nil, accessToken: nil)
        throw ProbeFailure.failed
    } catch ClientError.unexpectedRedirect { }
    do {
        _ = try await transport.request(profile: transportProfile, path: "/v1/fixture/oversized", body: nil, accessToken: nil)
        throw ProbeFailure.failed
    } catch ClientError.responseTooLarge { }
    for file in try FileManager.default.contentsOfDirectory(at: directory.root, includingPropertiesForKeys: nil) {
        let data = try Data(contentsOf: file)
        try require(!String(decoding: data, as: UTF8.self).contains("hox_"))
    }
    let summary: [String: Any] = ["schema_id": "hormuz.macos-local-proof", "schema_version": 1,
        "browser_login": true, "identity": true, "personal_usage": true, "client_isolation": true,
        "tenant_isolation": true, "refresh_rotation": true, "old_access_rejected": true,
        "logout_revocation": true, "connector_written": true, "redirect_rejected": true,
        "response_limit": true, "no_credentials_in_files": true, "credential_store": "in_memory_fixture"]
    let data = try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys])
    FileHandle.standardOutput.write(data + Data("\n".utf8))
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count == 3 else { exit(2) }
Task {
    do {
        try await verify(gateway: arguments[0], idp: arguments[1], root: arguments[2])
        exit(0)
    } catch {
        FileHandle.standardError.write(Data(("Local fixture verification failed: " + ClientError.message(for: error) + "\n").utf8))
        exit(1)
    }
}
dispatchMain()
