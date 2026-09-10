import Combine
import Foundation
import HormuzCompanionCore

@MainActor
final class CompanionStore: ObservableObject {
    @Published private(set) var snapshot: CompanionSnapshot
    @Published private(set) var scenario: DemoScenario
    @Published private(set) var visibilityMode: CompanionVisibilityMode
    @Published private(set) var uiScale: Double
    @Published private(set) var selectedDisplayID: String
    @Published private(set) var widgetVisible: Bool
    @Published private(set) var transientExpanded = false
    @Published private(set) var settingsHandleHovered = false
    @Published private(set) var refreshGeneration = 0

    let forceSettingsHover: Bool
    let initialDetailMetric: CompanionMetricID?
    let showSettingsOnLaunch: Bool
    let captureDirectory: String?
    let captureName: String
    let motionRecordingPath: String?

    private let defaults: UserDefaults
    private var foldTask: Task<Void, Never>?

    init(arguments: [String] = CommandLine.arguments, defaults: UserDefaults = .standard) {
        self.defaults = defaults

        let requestedScenario = Self.argumentValue(after: "--scenario", in: arguments)
            .flatMap(DemoScenario.init(rawValue:))
        let savedScenario = defaults.string(forKey: Keys.scenario)
            .flatMap(DemoScenario.init(rawValue:))
        let resolvedScenario = requestedScenario ?? savedScenario ?? .normal
        scenario = resolvedScenario

        if arguments.contains("--always") {
            visibilityMode = .always
        } else if arguments.contains("--folded") {
            visibilityMode = .fold
        } else {
            visibilityMode = defaults.string(forKey: Keys.visibility)
                .flatMap(CompanionVisibilityMode.init(rawValue:)) ?? .always
        }

        let savedScale = defaults.double(forKey: Keys.uiScale)
        let requestedScale = Self.argumentValue(after: "--ui-scale", in: arguments)
            .flatMap(Double.init)
        uiScale = Self.validScale(requestedScale ?? (savedScale == 0 ? 1 : savedScale))
        selectedDisplayID = defaults.string(forKey: Keys.display) ?? "primary"
        widgetVisible = !arguments.contains("--hidden")
        forceSettingsHover = arguments.contains("--settings-hover")
        initialDetailMetric = Self.argumentValue(after: "--show-detail", in: arguments)
            .flatMap(CompanionMetricID.init(rawValue:))
        showSettingsOnLaunch = arguments.contains("--show-settings")
        captureDirectory = Self.argumentValue(after: "--capture-dir", in: arguments)
        captureName = Self.argumentValue(after: "--capture-name", in: arguments)
            ?? requestedScenario?.rawValue
            ?? "normal"
        motionRecordingPath = Self.argumentValue(after: "--record-motion", in: arguments)
        snapshot = DemoSnapshotSource.snapshot(for: resolvedScenario)
    }

    func shouldExpand(hover: HoverCoordinator) -> Bool {
        visibilityMode == .always || transientExpanded || hover.selectedMetric != nil
    }

    func setVisibilityMode(_ mode: CompanionVisibilityMode) {
        visibilityMode = mode
        defaults.set(mode.rawValue, forKey: Keys.visibility)
        if mode == .always { transientExpanded = true }
    }

    func setTransientVisibilityMode(_ mode: CompanionVisibilityMode) {
        visibilityMode = mode
        transientExpanded = mode == .always
    }

    func setUIScale(_ value: Double) {
        uiScale = Self.validScale(value)
        defaults.set(uiScale, forKey: Keys.uiScale)
    }

    func setScenario(_ value: DemoScenario) {
        scenario = value
        snapshot = DemoSnapshotSource.snapshot(for: value)
        defaults.set(value.rawValue, forKey: Keys.scenario)
    }

    func setSelectedDisplayID(_ value: String) {
        selectedDisplayID = value
        defaults.set(value, forKey: Keys.display)
    }

    func ensureWidgetVisible() {
        widgetVisible = true
        transientExpanded = visibilityMode == .fold
    }

    func hideWidget() {
        widgetVisible = false
        transientExpanded = false
    }

    func refreshDemo() {
        snapshot = DemoSnapshotSource.snapshot(for: scenario)
        refreshGeneration += 1
    }

    func pointerEnteredWidget() {
        foldTask?.cancel()
        foldTask = nil
        if visibilityMode == .fold { transientExpanded = true }
    }

    func pointerExitedWidget(hover: HoverCoordinator) {
        guard visibilityMode == .fold else { return }
        foldTask?.cancel()
        foldTask = Task { [weak self, weak hover] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled, let self, let hover else { return }
            guard hover.selectedMetric == nil,
                  !hover.isPointerInsideTooltip,
                  !self.settingsHandleHovered
            else { return }
            self.transientExpanded = false
            self.foldTask = nil
        }
    }

    func settingsHandleHoverChanged(_ inside: Bool) {
        settingsHandleHovered = inside
        if inside {
            foldTask?.cancel()
            foldTask = nil
            transientExpanded = visibilityMode == .fold
        }
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
        static let scenario = "companion.demo.scenario"
        static let visibility = "companion.visibility"
        static let uiScale = "companion.uiScale"
        static let display = "companion.display"
    }
}
