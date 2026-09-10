import Foundation
import HormuzClientCore

extension ConnectionModel {
    var companionSnapshot: CompanionSnapshot {
        guard let dashboard else {
            return unavailableCompanionSnapshot
        }

        let usage = dashboard.usage
        let tokens = usage.inputTokens + usage.outputTokens
        let checked = dashboard.checkedAt.formatted(date: .omitted, time: .shortened)
        let readingStatus: CompanionReadingStatus
        if sessionState == .refreshPending || sessionExpired {
            readingStatus = .needsAuthentication
        } else if sessionState == .revocationPending {
            readingStatus = .offline
        } else {
            readingStatus = .current
        }
        let connectionMessage = readingStatus == .current ? nil : statusLabel

        return CompanionSnapshot(
            statusLabel: statusLabel,
            actorName: dashboard.identity.actorName,
            teamName: dashboard.identity.teamName,
            organizationName: dashboard.identity.organizationId,
            gatewayLabel: profile?.gateway ?? gateway,
            footerLine2: readingStatus == .current
                ? "Gateway only · Checked \(checked)"
                : "\(statusLabel) · Last checked \(checked)",
            metrics: [
                CompanionMetricReading(
                    id: .cost,
                    headlineText: Self.compactCurrency(usage.costUsd),
                    status: readingStatus,
                    blocks: [
                        CompanionDetailBlock(
                            id: "cost.month",
                            label: "Estimated cost",
                            trailingText: "This month",
                            valueText: usage.costUsd.formatted(.currency(code: "USD"))
                        ),
                        CompanionDetailBlock(
                            id: "cost.basis",
                            label: "Cost basis",
                            valueText: "Team rate card · Not a provider invoice"
                        ),
                    ],
                    message: connectionMessage
                ),
                CompanionMetricReading(
                    id: .tokens,
                    headlineText: Self.compactCount(tokens),
                    status: readingStatus,
                    blocks: [
                        CompanionDetailBlock(
                            id: "tokens.input",
                            label: "Input tokens",
                            trailingText: "This month",
                            valueText: usage.inputTokens.formatted()
                        ),
                        CompanionDetailBlock(
                            id: "tokens.output",
                            label: "Output tokens",
                            valueText: usage.outputTokens.formatted()
                        ),
                    ],
                    message: connectionMessage
                ),
                CompanionMetricReading(
                    id: .requests,
                    headlineText: Self.compactCount(usage.requests),
                    status: readingStatus,
                    blocks: [
                        CompanionDetailBlock(
                            id: "requests.month",
                            label: "Gateway requests",
                            trailingText: "This month",
                            valueText: usage.requests.formatted()
                        ),
                        CompanionDetailBlock(
                            id: "requests.denied",
                            label: "Governance outcomes",
                            valueText: "\(usage.deniedRequests.formatted()) denied · \(usage.rateLimitedRequests.formatted()) rate limited"
                        ),
                    ],
                    message: connectionMessage
                        ?? (usage.deniedRequests > 0
                            ? "\(usage.deniedRequests.formatted()) request\(usage.deniedRequests == 1 ? "" : "s") denied"
                            : nil)
                ),
            ]
        )
    }

    private var unavailableCompanionSnapshot: CompanionSnapshot {
        let status: CompanionReadingStatus
        if !hasSession || sessionState == .refreshPending || sessionExpired {
            status = .needsAuthentication
        } else if isBusy {
            status = .stale
        } else {
            status = .offline
        }

        let message = isBusy ? "Refreshing Hormuz…" : statusLabel
        let actor = profile == nil ? "Hormuz" : "Identity unavailable"
        let team = profile?.organization ?? "Open to connect"
        let metrics = CompanionMetricID.allCases.map { metric in
            CompanionMetricReading(
                id: metric,
                headlineText: "—",
                status: status,
                blocks: [],
                message: message
            )
        }
        return CompanionSnapshot(
            statusLabel: statusLabel,
            actorName: actor,
            teamName: team,
            organizationName: profile?.organization ?? organization,
            gatewayLabel: profile?.gateway ?? gateway,
            footerLine2: hasSession ? "Open Hormuz to refresh or reconnect" : "Open Hormuz to connect",
            metrics: metrics
        )
    }

    private var sessionExpired: Bool {
        guard let expiresAt else { return false }
        return expiresAt <= Date()
    }

    private static func compactCount(_ value: Int) -> String {
        switch value {
        case 1_000_000...:
            return compact(Double(value) / 1_000_000) + "M"
        case 1_000...:
            return compact(Double(value) / 1_000) + "K"
        default:
            return value.formatted()
        }
    }

    private static func compactCurrency(_ value: Double) -> String {
        if value >= 1_000 { return "$" + compact(value / 1_000) + "K" }
        if value >= 10 { return "$" + String(Int(value.rounded())) }
        return value.formatted(.currency(code: "USD").precision(.fractionLength(0...1)))
    }

    private static func compact(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(value >= 10 ? 0 : 1)))
    }
}

enum CompanionPreviewSnapshot {
    static func snapshot(named name: String) -> CompanionSnapshot? {
        switch name {
        case "connected": connected
        case "expired": unavailable(status: .needsAuthentication, label: "Session expired")
        case "offline": unavailable(status: .offline, label: "Gateway unavailable")
        case "empty": empty
        default: nil
        }
    }

    private static let connected = CompanionSnapshot(
        statusLabel: "Gateway verified",
        actorName: "Evaluation Owner",
        teamName: "Engineering",
        organizationName: "evaluation",
        gatewayLabel: "https://hormuz-https-preflight.onrender.com",
        footerLine2: "Gateway only · Checked just now",
        metrics: [
            CompanionMetricReading(
                id: .cost,
                headlineText: "$73",
                status: .current,
                blocks: [
                    CompanionDetailBlock(id: "cost.month", label: "Estimated cost", trailingText: "This month", valueText: "$73.00"),
                    CompanionDetailBlock(id: "cost.basis", label: "Cost basis", valueText: "Team rate card · Not a provider invoice"),
                ]
            ),
            CompanionMetricReading(
                id: .tokens,
                headlineText: "210K",
                status: .current,
                blocks: [
                    CompanionDetailBlock(id: "tokens.input", label: "Input tokens", trailingText: "This month", valueText: "168,000"),
                    CompanionDetailBlock(id: "tokens.output", label: "Output tokens", valueText: "42,000"),
                ]
            ),
            CompanionMetricReading(
                id: .requests,
                headlineText: "520",
                status: .current,
                blocks: [
                    CompanionDetailBlock(id: "requests.month", label: "Gateway requests", trailingText: "This month", valueText: "520"),
                    CompanionDetailBlock(id: "requests.denied", label: "Governance outcomes", valueText: "8 denied · 2 rate limited"),
                ],
                message: "8 requests denied"
            ),
        ]
    )

    private static let empty = CompanionSnapshot(
        statusLabel: "Gateway verified",
        actorName: "Evaluation Owner",
        teamName: "Engineering",
        organizationName: "evaluation",
        gatewayLabel: "https://hormuz-https-preflight.onrender.com",
        footerLine2: "Gateway only · Checked just now",
        metrics: CompanionMetricID.allCases.map {
            CompanionMetricReading(
                id: $0,
                headlineText: $0 == .cost ? "$0" : "0",
                status: .current,
                blocks: [],
                message: "No gateway usage this month"
            )
        }
    )

    private static func unavailable(
        status: CompanionReadingStatus,
        label: String
    ) -> CompanionSnapshot {
        CompanionSnapshot(
            statusLabel: label,
            actorName: "Identity unavailable",
            teamName: "evaluation",
            organizationName: "evaluation",
            gatewayLabel: "https://hormuz-https-preflight.onrender.com",
            footerLine2: "Open Hormuz to reconnect",
            metrics: CompanionMetricID.allCases.map {
                CompanionMetricReading(
                    id: $0,
                    headlineText: "—",
                    status: status,
                    blocks: [],
                    message: label
                )
            }
        )
    }
}
