import Combine

public enum EdgeHubPage: String, CaseIterable, Sendable {
    case home, connection, client, appearance, setup, review

    public var title: String {
        switch self {
        case .home: "Hormuz"
        case .connection: "Connection"
        case .client: "Client"
        case .appearance: "Appearance"
        case .setup: "Connect to Hormuz"
        case .review: "Review client setup"
        }
    }
}

/// Navigation is independent of session ownership; leaving a page never cancels
/// authentication or discards connection fields.
@MainActor
public final class EdgeHubNavigation: ObservableObject {
    @Published public private(set) var page: EdgeHubPage?
    public init() {}
    public var isOpen: Bool { page != nil }
    public func open(_ page: EdgeHubPage = .home) { self.page = page }
    public func close() { page = nil }
    public func back() {
        switch page {
        case .review: page = .client
        case .setup: page = .connection
        case .home, nil: page = nil
        default: page = .home
        }
    }
}
