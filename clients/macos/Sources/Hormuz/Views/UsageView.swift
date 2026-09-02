import HormuzClientCore
import SwiftUI

struct UsageView: View {
    let dashboard: Dashboard
    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Your usage this month").font(.headline)
                    Spacer()
                    Text("Gateway requests only").font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 30) {
                    metric("Requests", dashboard.usage.requests.formatted())
                    metric("Denied", dashboard.usage.deniedRequests.formatted())
                    metric("Estimated cost", dashboard.usage.costUsd.formatted(.currency(code: "USD")))
                }
                Text("\(dashboard.usage.inputTokens.formatted()) input tokens · \(dashboard.usage.outputTokens.formatted()) output tokens · \(dashboard.usage.redactions.formatted()) redactions")
                    .font(.callout).foregroundStyle(.secondary)
                Text("Cost uses your team's configured rate card. It is not a provider invoice. Checked \(dashboard.checkedAt.formatted(date: .omitted, time: .standard)).")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func metric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(value).font(.title2.monospacedDigit().weight(.semibold))
            Text(title).font(.caption).foregroundStyle(.secondary)
        }
    }
}
