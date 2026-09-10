import Combine
import Foundation
import HormuzClientCore

@MainActor
final class CompanionPresentationModel: ObservableObject {
    @Published private(set) var visibilityMode: CompanionVisibilityMode
    @Published private(set) var uiScale: Double
    @Published private(set) var selectedDisplayID: String
    @Published private(set) var widgetVisible: Bool
    @Published private(set) var transientExpanded = false
    @Published private(set) var settingsHandleHovered = false
    let hub = EdgeHubNavigation()

    let initialDetailMetric: CompanionMetricID?
    let showControlCenterOnLaunch: Bool
    let captureDirectory: String?
    let captureName: String
    let previewSnapshot: CompanionSnapshot?
    let initialHubPage: EdgeHubPage?

    private let defaults: UserDefaults
    private var foldTask: Task<Void, Never>?
    private var keepsWidgetOpenAfterDismissal = false

    init(arguments: [String] = CommandLine.arguments, defaults: UserDefaults = .standard) {
        self.defaults = defaults

        if arguments.contains("--companion-always") {
            visibilityMode = .always
        } else if arguments.contains("--companion-folded") {
            visibilityMode = .fold
        } else {
            visibilityMode = defaults.string(forKey: Keys.visibility)
                .flatMap(CompanionVisibilityMode.init(rawValue:)) ?? .always
        }

        let savedScale = defaults.double(forKey: Keys.uiScale)
        let requestedScale = Self.argumentValue(after: "--companion-scale", in: arguments)
            .flatMap(Double.init)
        uiScale = Self.validScale(requestedScale ?? (savedScale == 0 ? 1 : savedScale))
        selectedDisplayID = defaults.string(forKey: Keys.display) ?? "primary"
        widgetVisible = !arguments.contains("--companion-hidden")
        initialDetailMetric = Self.argumentValue(after: "--companion-show-detail", in: arguments)
            .flatMap(CompanionMetricID.init(rawValue:))
        showControlCenterOnLaunch = arguments.contains("--companion-open")
        captureDirectory = Self.argumentValue(after: "--companion-capture-dir", in: arguments)
        captureName = Self.argumentValue(after: "--companion-capture-name", in: arguments)
            ?? "hormuz-live-companion"
        previewSnapshot = Self.argumentValue(after: "--companion-preview", in: arguments)
            .flatMap(CompanionPreviewSnapshot.snapshot)
        initialHubPage = Self.argumentValue(after: "--companion-hub", in: arguments)
            .flatMap(EdgeHubPage.init(rawValue:))
    }

    func snapshot(for connection: ConnectionModel) -> CompanionSnapshot {
        previewSnapshot ?? connection.companionSnapshot
    }

    func shouldExpand(hover: HoverCoordinator) -> Bool {
        visibilityMode == .always || transientExpanded || hover.selectedMetric != nil || hub.isOpen
    }

    func setVisibilityMode(_ mode: CompanionVisibilityMode) {
        keepsWidgetOpenAfterDismissal = false
        visibilityMode = mode
        defaults.set(mode.rawValue, forKey: Keys.visibility)
        transientExpanded = false
    }

    func setUIScale(_ value: Double) {
        uiScale = Self.validScale(value)
        defaults.set(uiScale, forKey: Keys.uiScale)
    }

    func setSelectedDisplayID(_ value: String) {
        selectedDisplayID = value
        defaults.set(value, forKey: Keys.display)
    }

    func ensureWidgetVisible() {
        keepsWidgetOpenAfterDismissal = false
        widgetVisible = true
        transientExpanded = visibilityMode == .fold
    }

    func hideWidget() {
        keepsWidgetOpenAfterDismissal = false
        hub.close()
        widgetVisible = false
        transientExpanded = false
    }

    func pointerEnteredWidget() {
        keepsWidgetOpenAfterDismissal = false
        foldTask?.cancel()
        foldTask = nil
        if visibilityMode == .fold { transientExpanded = true }
    }

    func pointerExitedWidget(hover: HoverCoordinator) {
        guard visibilityMode == .fold, !keepsWidgetOpenAfterDismissal else { return }
        foldTask?.cancel()
        foldTask = Task { [weak self, weak hover] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled, let self, let hover else { return }
            guard hover.selectedMetric == nil,
                  !self.keepsWidgetOpenAfterDismissal,
                  !hover.isPointerInsideTooltip,
                  !self.hub.isOpen,
                  !self.settingsHandleHovered else { return }
            self.transientExpanded = false
            self.foldTask = nil
        }
    }

    func settingsHandleHoverChanged(_ inside: Bool) {
        settingsHandleHovered = inside
        if inside {
            keepsWidgetOpenAfterDismissal = false
            foldTask?.cancel()
            foldTask = nil
            transientExpanded = visibilityMode == .fold
        }
    }

    /// An outside click dismisses the card, not the expanded widget. Normal
    /// auto-folding resumes after the next pointer visit or visibility command.
    func keepWidgetOpenAfterCardDismissal() {
        foldTask?.cancel()
        foldTask = nil
        keepsWidgetOpenAfterDismissal = true
        widgetVisible = true
        transientExpanded = true
    }

    private static func argumentValue(after flag: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else {
            return nil
        }
        return arguments[index + 1]
    }

    private static func validScale(_ value: Double) -> Double {
        [1.0, 1.25, 1.5].min(by: { abs($0 - value) < abs($1 - value) }) ?? 1
    }

    private enum Keys {
        static let visibility = "companion.visibility"
        static let uiScale = "companion.uiScale"
        static let display = "companion.display"
    }
}
