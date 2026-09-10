import HormuzClientCore
import SwiftUI

struct EdgeHubView: View {
    @Bindable var connection: ConnectionModel
    @ObservedObject var presentation: CompanionPresentationModel
    @ObservedObject var navigation: EdgeHubNavigation
    let displays: () -> [DisplayOption]
    let openExpanded: () -> Void
    let close: () -> Void

    private var scale: CGFloat { presentation.uiScale }
    private var page: EdgeHubPage { navigation.page ?? .home }
    private var needsReconnect: Bool {
        connection.sessionState == .refreshPending || (connection.expiresAt.map { $0 <= Date() } ?? false)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10 * scale) {
                if page != .home {
                    Button(action: navigation.back) { Image(systemName: "chevron.left") }
                        .accessibilityLabel("Back")
                } else {
                    HormuzBrandMark()
                        .frame(width: 24 * scale, height: 24 * scale)
                        .foregroundStyle(CompanionPalette.brandSignal)
                }
                Text(page.title).font(.system(size: 18 * scale, weight: .semibold))
                Spacer()
                Button(action: close) { Image(systemName: "xmark") }
                    .accessibilityLabel("Close Hormuz controls")
            }
            .buttonStyle(.plain)
            .padding(.bottom, 18 * scale)

            ScrollView {
                VStack(alignment: .leading, spacing: 18 * scale) {
                    if let message = connection.message {
                        HStack(alignment: .top) {
                            Text(message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            Button { connection.message = nil } label: { Image(systemName: "xmark") }
                                .buttonStyle(.plain).accessibilityLabel("Dismiss message")
                        }
                        .padding(10 * scale)
                        .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 10 * scale))
                    }
                    switch page {
                    case .home: home
                    case .connection, .setup:
                        EdgeConnectionView(connection: connection, navigation: navigation, scale: scale,
                                           showSetup: page == .setup)
                    case .client, .review:
                        EdgeClientView(connection: connection, navigation: navigation, scale: scale,
                                       showReview: page == .review)
                    case .appearance:
                        EdgeAppearanceView(presentation: presentation, displays: displays(), scale: scale)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.trailing, 3 * scale)
            }
            .scrollIndicators(.automatic)

            HStack {
                Label("Gateway only", systemImage: "shield.lefthalf.filled")
                Spacer()
                Button(action: openExpanded) { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open expanded control center")
                    .help("Open expanded control center")
            }
            .font(.system(size: 11 * scale))
            .foregroundStyle(.secondary)
            .padding(.top, 16 * scale)
        }
        .font(.system(size: 13 * scale))
        .controlSize(scale >= 1.25 ? .large : .regular)
        .padding(20 * scale)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.black, in: RoundedRectangle(cornerRadius: 24 * scale))
        .overlay(RoundedRectangle(cornerRadius: 24 * scale).strokeBorder(.white.opacity(0.10)))
        .padding(8 * scale)
        .preferredColorScheme(.dark)
        .tint(CompanionPalette.live)
        .onExitCommand(perform: navigation.back)
        .accessibilityIdentifier("hormuz-edge-controls")
    }

    private var home: some View {
        VStack(alignment: .leading, spacing: 18 * scale) {
            if presentation.previewSnapshot != nil {
                Text("VISUAL PREVIEW · NO SESSION")
                    .font(.system(size: 10 * scale, weight: .medium)).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 6 * scale) {
                Text(connection.dashboard?.identity.actorName ?? "Your AI, governed.")
                    .font(.system(size: 21 * scale, weight: .semibold))
                Text(connection.dashboard.map { "\($0.identity.teamName) · \($0.identity.organizationId)" }
                     ?? "Connect your tools. Keep access close.")
                    .foregroundStyle(.secondary)
                Label(connection.statusLabel, systemImage: needsReconnect || connection.sessionState == .revocationPending
                      ? "exclamationmark.circle" : connection.dashboard == nil ? "circle.dotted" : "checkmark.shield")
                    .foregroundStyle(needsReconnect || connection.sessionState == .revocationPending
                                     ? Color.orange : connection.dashboard == nil ? Color.secondary : CompanionPalette.live)
                    .padding(.top, 5 * scale)
            }
            VStack(spacing: 2 * scale) {
                destination(.connection, symbol: "person.crop.circle", subtitle: "Identity & secure session")
                destination(.client, symbol: "terminal", subtitle: "Client setup & launcher")
                destination(.appearance, symbol: "slider.horizontal.3", subtitle: "Size, display & visibility")
            }
            if !connection.hasSession {
                Button("Connect to Hormuz") { navigation.open(.setup) }
                    .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
            } else if needsReconnect || connection.sessionState == .revocationPending {
                Button("Review connection") { navigation.open(.connection) }
                    .buttonStyle(EdgePrimaryButtonStyle(scale: scale))
            } else {
                Button("Refresh status") { connection.refresh() }
                    .disabled(connection.isBusy)
                    .buttonStyle(.bordered)
            }
        }
    }

    private func destination(_ destination: EdgeHubPage, symbol: String, subtitle: String) -> some View {
        Button { navigation.open(destination) } label: {
            HStack(spacing: 12 * scale) {
                Image(systemName: symbol).font(.system(size: 18 * scale)).frame(width: 24 * scale)
                VStack(alignment: .leading, spacing: 3 * scale) {
                    Text(destination.title).fontWeight(.medium)
                    Text(subtitle).font(.system(size: 11 * scale)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.system(size: 10 * scale)).foregroundStyle(.secondary)
            }
            .padding(.vertical, 13 * scale).padding(.horizontal, 10 * scale)
            .contentShape(Rectangle())
        }
        .buttonStyle(EdgeHubRowStyle())
    }
}

private struct EdgeHubRowStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(.white.opacity(configuration.isPressed ? 0.12 : 0.045),
                        in: RoundedRectangle(cornerRadius: 10))
    }
}
