import Foundation

public enum DemoSnapshotSource {
    public static let frozenDate = Date(timeIntervalSince1970: 1_788_782_400)

    public static func snapshot(for scenario: DemoScenario) -> CompanionSnapshot {
        let reset = "Resets Oct 1 UTC"
        let baseMetrics = [
            CompanionMetricReading(
                id: .budget,
                usedFraction: 0.73,
                headlineText: "73%",
                status: .current,
                blocks: [
                    CompanionDetailBlock(
                        id: "budget.month",
                        label: "Monthly budget",
                        trailingText: reset,
                        usedFraction: 0.73,
                        valueText: "$73 of $100 used"
                    ),
                    CompanionDetailBlock(
                        id: "budget.tokens",
                        label: "Monthly tokens",
                        trailingText: reset,
                        usedFraction: 0.21,
                        valueText: "210K of 1M used"
                    )
                ]
            ),
            CompanionMetricReading(
                id: .tokens,
                usedFraction: 0.21,
                headlineText: "21%",
                status: .current,
                blocks: [
                    CompanionDetailBlock(
                        id: "tokens.month",
                        label: "Monthly tokens",
                        trailingText: reset,
                        usedFraction: 0.21,
                        valueText: "210K of 1M used"
                    ),
                    CompanionDetailBlock(
                        id: "tokens.budget",
                        label: "Monthly budget",
                        trailingText: reset,
                        usedFraction: 0.73,
                        valueText: "$73 of $100 used"
                    )
                ]
            ),
            CompanionMetricReading(
                id: .requests,
                usedFraction: 0.52,
                headlineText: "52%",
                status: .current,
                blocks: [
                    CompanionDetailBlock(
                        id: "requests.month",
                        label: "Monthly requests",
                        trailingText: reset,
                        usedFraction: 0.52,
                        valueText: "520 of 1,000 used"
                    ),
                    CompanionDetailBlock(
                        id: "requests.denied",
                        label: "Denied requests",
                        trailingText: nil,
                        usedFraction: nil,
                        valueText: "8 requests denied"
                    )
                ]
            )
        ]

        switch scenario {
        case .normal:
            return make(scenario: scenario, footer: "Gateway only · Estimated cost", metrics: baseMetrics)
        case .empty:
            let metrics = baseMetrics.map { reading in
                CompanionMetricReading(
                    id: reading.id,
                    usedFraction: 0,
                    headlineText: "0%",
                    status: .current,
                    blocks: reading.blocks.map { block in
                        CompanionDetailBlock(
                            id: block.id,
                            label: block.label,
                            trailingText: block.trailingText,
                            usedFraction: block.usedFraction == nil ? nil : 0,
                            valueText: zeroText(for: reading.id, blockID: block.id)
                        )
                    },
                    message: "No requests this month"
                )
            }
            return make(scenario: scenario, footer: "Gateway only · Estimated cost", metrics: metrics)
        case .exhausted:
            var metrics = baseMetrics
            metrics[0] = CompanionMetricReading(
                id: .budget,
                usedFraction: 1,
                headlineText: "100%",
                status: .current,
                blocks: [
                    CompanionDetailBlock(
                        id: "budget.month",
                        label: "Monthly budget",
                        trailingText: reset,
                        usedFraction: 1,
                        valueText: "$100 of $100 used"
                    ),
                    CompanionDetailBlock(
                        id: "budget.tokens",
                        label: "Monthly tokens",
                        trailingText: reset,
                        usedFraction: 0.21,
                        valueText: "210K of 1M used"
                    )
                ],
                message: "Budget reached"
            )
            return make(scenario: scenario, footer: "Gateway only · Estimated cost", metrics: metrics)
        case .stale:
            let metrics = baseMetrics.map {
                CompanionMetricReading(
                    id: $0.id,
                    usedFraction: $0.usedFraction,
                    headlineText: $0.headlineText,
                    status: .stale,
                    blocks: $0.blocks,
                    message: $0.message
                )
            }
            return make(scenario: scenario, footer: "Last updated 10 minutes ago", metrics: metrics)
        case .offline:
            return unavailable(
                scenario: scenario,
                status: .offline,
                message: "Gateway unavailable"
            )
        case .needsAuth:
            return unavailable(
                scenario: scenario,
                status: .needsAuth,
                message: "Reconnect to Hormuz"
            )
        case .unsupported:
            return unavailable(
                scenario: scenario,
                status: .unsupported,
                message: "Limit data unavailable"
            )
        }
    }

    private static func make(
        scenario: DemoScenario,
        footer: String,
        metrics: [CompanionMetricReading]
    ) -> CompanionSnapshot {
        CompanionSnapshot(
            scenario: scenario,
            actorName: "Alice",
            teamName: "Engineering",
            organizationName: "Acme",
            footerLine2: footer,
            metrics: metrics
        )
    }

    private static func unavailable(
        scenario: DemoScenario,
        status: CompanionReadingStatus,
        message: String
    ) -> CompanionSnapshot {
        let metrics = CompanionMetricID.allCases.map {
            CompanionMetricReading(
                id: $0,
                usedFraction: nil,
                headlineText: "—",
                status: status,
                blocks: [],
                message: message
            )
        }
        return make(scenario: scenario, footer: "Demo data · Not connected", metrics: metrics)
    }

    private static func zeroText(for metric: CompanionMetricID, blockID: String) -> String {
        if blockID.contains("denied") { return "0 requests denied" }
        switch metric {
        case .budget: return blockID.contains("tokens") ? "0 of 1M used" : "$0 of $100 used"
        case .tokens: return blockID.contains("budget") ? "$0 of $100 used" : "0 of 1M used"
        case .requests: return "0 of 1,000 used"
        }
    }
}
