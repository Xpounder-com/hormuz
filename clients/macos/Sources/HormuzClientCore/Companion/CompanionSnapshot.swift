import Foundation

public enum CompanionMetricID: String, CaseIterable, Codable, Sendable, Identifiable {
    case cost
    case tokens
    case requests

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .cost: "Cost"
        case .tokens: "Tokens"
        case .requests: "Requests"
        }
    }

    public var symbolName: String {
        switch self {
        case .cost: "dollarsign"
        case .tokens: "number"
        case .requests: "arrow.up.arrow.down"
        }
    }
}

public enum CompanionVisibilityMode: String, CaseIterable, Codable, Sendable, Identifiable {
    case always
    case fold

    public var id: String { rawValue }
    public var displayName: String { self == .always ? "Always visible" : "Fold when idle" }
}

public enum CompanionReadingStatus: Equatable, Sendable {
    case current
    case stale
    case offline
    case needsAuthentication

    public var isStale: Bool { self == .stale }
    public var isUnavailable: Bool {
        switch self {
        case .offline, .needsAuthentication: true
        case .current, .stale: false
        }
    }
}

public enum CompanionUsageBand: String, Equatable, Sendable {
    case ample
    case watch
    case critical
    case exhausted

    public static func band(for usedFraction: Double) -> CompanionUsageBand {
        switch usedFraction {
        case ..<0.50: .ample
        case ..<0.70: .watch
        case ..<1.00: .critical
        default: .exhausted
        }
    }
}

public struct CompanionDetailBlock: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let trailingText: String?
    public let usedFraction: Double?
    public let valueText: String

    public init(
        id: String,
        label: String,
        trailingText: String? = nil,
        usedFraction: Double? = nil,
        valueText: String
    ) {
        self.id = id
        self.label = label
        self.trailingText = trailingText
        self.usedFraction = usedFraction
        self.valueText = valueText
    }
}

public struct CompanionMetricReading: Identifiable, Equatable, Sendable {
    public let id: CompanionMetricID
    public let usedFraction: Double?
    public let headlineText: String
    public let status: CompanionReadingStatus
    public let blocks: [CompanionDetailBlock]
    public let message: String?

    public var showsHeadline: Bool {
        !["", "—", "–", "-"].contains(headlineText.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    public init(
        id: CompanionMetricID,
        usedFraction: Double? = nil,
        headlineText: String,
        status: CompanionReadingStatus,
        blocks: [CompanionDetailBlock],
        message: String? = nil
    ) {
        self.id = id
        self.usedFraction = usedFraction
        self.headlineText = headlineText
        self.status = status
        self.blocks = blocks
        self.message = message
    }
}

public struct CompanionSnapshot: Equatable, Sendable {
    public let statusLabel: String
    public let actorName: String
    public let teamName: String
    public let organizationName: String
    public let gatewayLabel: String
    public let footerLine2: String
    public let metrics: [CompanionMetricReading]

    public init(
        statusLabel: String,
        actorName: String,
        teamName: String,
        organizationName: String,
        gatewayLabel: String,
        footerLine2: String,
        metrics: [CompanionMetricReading]
    ) {
        self.statusLabel = statusLabel
        self.actorName = actorName
        self.teamName = teamName
        self.organizationName = organizationName
        self.gatewayLabel = gatewayLabel
        self.footerLine2 = footerLine2
        self.metrics = metrics
    }

    public func metric(_ id: CompanionMetricID) -> CompanionMetricReading? {
        metrics.first { $0.id == id }
    }
}
