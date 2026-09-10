import AppKit
import Combine
import HormuzClientCore
import Observation
import QuartzCore
import SwiftUI

struct DisplayOption: Identifiable, Hashable {
    let id: String
    let name: String
}

private final class OverlayPanel: NSPanel {
    private let acceptsKeyboard: Bool
    init(acceptsKeyboard: Bool = false) {
        self.acceptsKeyboard = acceptsKeyboard
        super.init(
            contentRect: .zero,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        isMovable = false
        isMovableByWindowBackground = false
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        isReleasedWhenClosed = false
        animationBehavior = .none
    }

    override var canBecomeKey: Bool { acceptsKeyboard }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class EdgePanelController {
    let connection: ConnectionModel
    let presentation: CompanionPresentationModel
    let hover: HoverCoordinator

    private let openControlCenterAction: () -> Void
    private let bodyPanel = OverlayPanel()
    private let tooltipPanel = OverlayPanel()
    private let settingsHandlePanel = OverlayPanel()
    private let hubPanel = OverlayPanel(acceptsKeyboard: true)
    private lazy var bodyTransition = EdgeSurfaceTransition(panel: bodyPanel)
    private lazy var tooltipTransition = PanelVisibilityTransition(panel: tooltipPanel)
    private lazy var handleTransition = PanelVisibilityTransition(panel: settingsHandlePanel)
    private lazy var hubTransition = PanelVisibilityTransition(panel: hubPanel)
    private let settledUpdate = SettledPanelUpdate()
    private var cancellables = Set<AnyCancellable>()
    private var screenObserver: NSObjectProtocol?
    private var globalMouseMonitor: Any?
    private var localMouseMonitor: Any?
    private var localKeyMonitor: Any?

    init(
        connection: ConnectionModel,
        presentation: CompanionPresentationModel,
        hover: HoverCoordinator,
        openControlCenter: @escaping () -> Void
    ) {
        self.connection = connection
        self.presentation = presentation
        self.hover = hover
        openControlCenterAction = openControlCenter
    }

    func start() {
        // The handle overlaps the body's transparent curl. Keep that transparent
        // window from intercepting clicks when the body is reordered during fades.
        settingsHandlePanel.level = NSWindow.Level(rawValue: bodyPanel.level.rawValue + 1)
        installContent()
        installObservers()
        observeConnection()
        updatePanels(animated: false)

        if let page = presentation.initialHubPage { showHub(page) }

        if let metric = presentation.initialDetailMetric {
            presentation.ensureWidgetVisible()
            hover.showAndPin(metric)
        }

        if let captureDirectory = presentation.captureDirectory {
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 900_000_000)
                guard let self else { return }
                do {
                    try PanelEvidenceCapture.capture(
                        panels: self.visibleEvidencePanels,
                        directory: URL(fileURLWithPath: captureDirectory, isDirectory: true),
                        name: self.presentation.captureName
                    )
                } catch {
                    NSLog("Hormuz companion evidence capture failed: %@", error.localizedDescription)
                }
            }
        }

        if presentation.previewSnapshot != nil,
           CommandLine.arguments.contains("--companion-layout-audit"),
           let directory = presentation.captureDirectory {
            Task { [weak self] in await self?.auditLiveLayout(directory: directory) }
        }
        if presentation.previewSnapshot != nil,
           CommandLine.arguments.contains("--companion-motion-audit"),
           let directory = presentation.captureDirectory {
            Task { [weak self] in await self?.auditSurfaceMotion(directory: directory) }
        }
    }

    func stop() {
        settledUpdate.cancel()
        cancellables.removeAll()
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        if let globalMouseMonitor { NSEvent.removeMonitor(globalMouseMonitor) }
        if let localMouseMonitor { NSEvent.removeMonitor(localMouseMonitor) }
        if let localKeyMonitor { NSEvent.removeMonitor(localKeyMonitor) }
        screenObserver = nil
        globalMouseMonitor = nil
        localMouseMonitor = nil
        bodyTransition.hide(immediate: true)
        tooltipTransition.setVisible(false, immediate: true)
        handleTransition.setVisible(false, immediate: true)
        hubTransition.setVisible(false, immediate: true)
    }

    func showHub(_ page: EdgeHubPage = .home) {
        hover.dismiss()
        presentation.ensureWidgetVisible()
        presentation.hub.open(page)
        updatePanels(animated: false)
        hubPanel.makeKeyAndOrderFront(nil)
    }

    private func closeHub() {
        presentation.hub.close()
        presentation.pointerExitedWidget(hover: hover)
    }

    func showWidget() {
        presentation.ensureWidgetVisible()
        updatePanels(animated: true)
    }

    func hideWidget() {
        hover.dismiss()
        presentation.hideWidget()
    }

    func showAndPin(_ metric: CompanionMetricID) {
        closeHub()
        presentation.ensureWidgetVisible()
        hover.showAndPin(metric)
    }

    var displayOptions: [DisplayOption] {
        NSScreen.screens.enumerated().map { index, screen in
            DisplayOption(
                id: Self.identifier(for: screen),
                name: "Display \(index + 1) · \(Int(screen.frame.width))×\(Int(screen.frame.height))"
            )
        }
    }

    private func installContent() {
        bodyPanel.contentView = hostingView(
            EdgeNotchView(
                connection: connection,
                presentation: presentation,
                hover: hover,
                surface: bodyTransition.state,
                openControlCenter: { [weak self] in self?.showHub() },
                hideWidget: { [weak self] in self?.hideWidget() },
                quit: { NSApplication.shared.terminate(nil) }
            )
        )

        tooltipPanel.contentView = hostingView(
            UsageTooltip(
                connection: connection,
                presentation: presentation,
                hover: hover,
                openControlCenter: { [weak self] in self?.showHub() }
            )
        )

        settingsHandlePanel.contentView = hostingView(
            SettingsHandle(
                presentation: presentation,
                openControlCenter: { [weak self] in self?.showHub() }
            )
        )
        hubPanel.title = "Hormuz controls"
        hubPanel.contentView = hostingView(EdgeHubView(
            connection: connection,
            presentation: presentation,
            navigation: presentation.hub,
            displays: { [weak self] in self?.displayOptions ?? [] },
            openExpanded: { [weak self] in
                self?.closeHub()
                self?.openControlCenterAction()
            },
            close: { [weak self] in self?.closeHub() }
        ))
    }

    private func hostingView<Content: View>(_ content: Content) -> NSHostingView<Content> {
        let view = NSHostingView(rootView: content)
        view.autoresizingMask = [.width, .height]
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.clear.cgColor
        return view
    }

    private func installObservers() {
        Publishers.Merge3(presentation.objectWillChange, hover.objectWillChange, presentation.hub.objectWillChange)
            .sink { [weak self] _ in self?.schedulePanelUpdate() }
            .store(in: &cancellables)

        localKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.keyCode == 53 else { return event }
            if self.presentation.hub.isOpen {
                self.presentation.hub.back()
                self.presentation.pointerExitedWidget(hover: self.hover)
                return nil
            }
            self.hover.dismiss()
            return event
        }

        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updatePanels(animated: false) }
        }

        globalMouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) {
            [weak self] _ in
            let point = NSEvent.mouseLocation
            Task { @MainActor in self?.dismissCardIfClickIsOutside(at: point) }
        }
        localMouseMonitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) {
            [weak self] event in
            // Picker menus can extend beyond the card. Let them finish their
            // selection without treating their own window as an outside click.
            if let window = event.window, window.level.rawValue >= NSWindow.Level.popUpMenu.rawValue {
                return event
            }
            let point = event.window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
            self?.dismissCardIfClickIsOutside(at: point)
            return event
        }
    }

    private func schedulePanelUpdate() {
        settledUpdate.schedule { [weak self] in self?.updatePanels(animated: false) }
    }

    private func observeConnection() {
        withObservationTracking {
            _ = connection.companionSnapshot
        } onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.observeConnection()
                self.updatePanels(animated: true)
            }
        }
    }

    private func dismissCardIfClickIsOutside(at point: NSPoint) {
        guard presentation.hub.isOpen || hover.selectedMetric != nil else { return }
        let card = presentation.hub.isOpen ? hubPanel : tooltipPanel
        let interactiveFrames = [bodyPanel, card, settingsHandlePanel]
            .filter(\.isVisible)
            .map(\.frame)
        guard !interactiveFrames.contains(where: { $0.contains(point) }) else { return }
        presentation.hub.close()
        hover.dismiss()
        presentation.keepWidgetOpenAfterCardDismissal()
    }

    private func updatePanels(animated: Bool) {
        guard presentation.widgetVisible else {
            bodyTransition.hide()
            tooltipTransition.setVisible(false)
            handleTransition.setVisible(false)
            hubTransition.setVisible(false)
            return
        }

        let snapshot = presentation.snapshot(for: connection)
        let layout = CompanionLayout(uiScale: presentation.uiScale)
        let visibleFrame = selectedScreen.visibleFrame
        let expanded = presentation.shouldExpand(hover: hover)
        let bodyFrame = makeBodyFrame(
            layout: layout,
            visibleFrame: visibleFrame,
            metrics: snapshot.metrics,
            expanded: expanded
        )

        bodyTransition.show(frame: bodyFrame, expanded: expanded)

        if expanded {
            let orbFrame = makeSettingsHandleFrame(layout: layout, bodyFrame: bodyFrame)
            setFrame(orbFrame, for: settingsHandlePanel, animated: animated)
            handleTransition.setVisible(true)
        } else {
            handleTransition.setVisible(false)
        }

        // Hover dismissal can finish after the first fold delay. Schedule a new
        // fold once the detail is gone so the widget cannot get stuck expanded.
        if presentation.visibilityMode == .fold, presentation.transientExpanded,
           !presentation.hub.isOpen, hover.selectedMetric == nil,
           !bodyPanel.frame.contains(NSEvent.mouseLocation),
           !settingsHandlePanel.frame.contains(NSEvent.mouseLocation) {
            presentation.pointerExitedWidget(hover: hover)
        }

        if presentation.hub.isOpen {
            tooltipTransition.setVisible(false)
            let scale = presentation.uiScale
            let width = min(336 * scale, max(240, visibleFrame.width - bodyFrame.width - 32))
            let height = min(480 * scale, visibleFrame.height - 24)
            let y = CGFloat(CompanionGeometry.clampedTooltipOrigin(
                proposed: Double(bodyFrame.minY - 20 * scale), tooltipLength: Double(height),
                visibleMinimum: Double(visibleFrame.minY), visibleMaximum: Double(visibleFrame.maxY)))
            setFrame(NSRect(x: bodyFrame.minX - width - 8, y: y, width: width, height: height),
                     for: hubPanel, animated: false)
            hubTransition.setVisible(true)
            return
        }
        hubTransition.setVisible(false)

        guard expanded,
              let metric = hover.selectedMetric,
              let index = snapshot.metrics.firstIndex(where: { $0.id == metric }),
              let reading = snapshot.metric(metric) else {
            tooltipTransition.setVisible(false)
            return
        }

        let tooltipFrame = makeTooltipFrame(
            layout: layout,
            bodyFrame: bodyFrame,
            visibleFrame: visibleFrame,
            metricIndex: index,
            metrics: snapshot.metrics,
            reading: reading
        )
        setFrame(tooltipFrame, for: tooltipPanel, animated: animated)
        tooltipTransition.setVisible(true)
    }

    private func setFrame(_ frame: NSRect, for panel: NSPanel, animated: Bool) {
        // Never stretch a host at the new scale through an old window size, or
        // let an obsolete animation completion restore an earlier frame.
        guard panel.frame != frame else { return }
        panel.setFrame(frame, display: false)
        // AppKit aligns borderless windows to backing pixels. Size the host from
        // that resulting frame, rather than retaining fractional requested bounds.
        synchronizeContentView(of: panel, to: panel.frame.size)
    }

    private func synchronizeContentView(of panel: NSPanel, to size: NSSize) {
        guard let contentView = panel.contentView else { return }
        contentView.frame = NSRect(origin: .zero, size: size)
        contentView.needsLayout = true
        contentView.layoutSubtreeIfNeeded()
        contentView.needsDisplay = true
    }

    private func makeBodyFrame(
        layout: CompanionLayout,
        visibleFrame: NSRect,
        metrics: [CompanionMetricReading],
        expanded: Bool
    ) -> NSRect {
        let width = ceil(expanded ? layout.sideBodyDepth : layout.pillHotZone)
        let height = ceil(expanded ? layout.shapeHeight(metrics: metrics) : layout.pillHeight)
        return NSRect(
            x: visibleFrame.maxX - width,
            y: visibleFrame.midY - height / 2,
            width: width,
            height: height
        )
    }

    private func makeTooltipFrame(
        layout: CompanionLayout,
        bodyFrame: NSRect,
        visibleFrame: NSRect,
        metricIndex: Int,
        metrics: [CompanionMetricReading],
        reading: CompanionMetricReading
    ) -> NSRect {
        let ringCenterY = bodyFrame.maxY - layout.ringCenterFromTop(index: metricIndex, metrics: metrics)
        let tooltipHeight = layout.tooltipPanelHeight(for: reading)
        let proposedY = ringCenterY - tooltipHeight / 2
        let originY = CGFloat(CompanionGeometry.clampedTooltipOrigin(
            proposed: Double(proposedY),
            tooltipLength: Double(tooltipHeight),
            visibleMinimum: Double(visibleFrame.minY),
            visibleMaximum: Double(visibleFrame.maxY),
            inset: 12
        ))
        return NSRect(
            x: bodyFrame.minX - layout.tailGap - layout.tooltipPanelWidth + layout.panelShadowInset,
            y: originY,
            width: layout.tooltipPanelWidth,
            height: tooltipHeight
        )
    }

    private func makeSettingsHandleFrame(layout: CompanionLayout, bodyFrame: NSRect) -> NSRect {
        let center = CGPoint(x: bodyFrame.maxX - layout.curlRadius, y: bodyFrame.minY)
        return NSRect(
            x: center.x - layout.orbHotZone / 2,
            y: center.y - layout.orbHotZone / 2,
            width: layout.orbHotZone,
            height: layout.orbHotZone
        )
    }

    private var selectedScreen: NSScreen {
        if presentation.selectedDisplayID != "primary",
           let match = NSScreen.screens.first(where: { Self.identifier(for: $0) == presentation.selectedDisplayID }) {
            return match
        }
        return NSScreen.screens.first ?? NSScreen.main ?? NSScreen()
    }

    private var visibleEvidencePanels: [(name: String, panel: NSPanel)] {
        [
            ("body", bodyPanel),
            ("tooltip", tooltipPanel),
            ("control", settingsHandlePanel),
            ("hub", hubPanel),
        ].filter { $0.panel.isVisible }
    }

    private static func identifier(for screen: NSScreen) -> String {
        if let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber {
            return number.stringValue
        }
        return "screen-\(Int(screen.frame.origin.x))-\(Int(screen.frame.origin.y))"
    }

    /// Exercises native window updates through the same preference setters as
    /// the UI. Only available in explicit fixture mode; records geometry, no session data.
    private func auditLiveLayout(directory: String) async {
        let savedScale = presentation.uiScale
        let savedVisibility = presentation.visibilityMode
        defer {
            presentation.setUIScale(savedScale)
            presentation.setVisibilityMode(savedVisibility)
        }
        var measurements: [[String: Any]] = []
        presentation.setVisibilityMode(.always)
        showHub(.appearance)
        for (index, scale) in [1.0, 1.25, 1.5, 1.25, 1.0].enumerated() {
            presentation.setUIScale(scale)
            try? await Task.sleep(nanoseconds: 300_000_000)
            let expectedWidth = ceil(CompanionLayout(uiScale: scale).sideBodyDepth)
            let valid = abs(bodyPanel.frame.width - expectedWidth) < 0.5
                && abs(bodyPanel.frame.maxX - selectedScreen.visibleFrame.maxX) < 0.5
                && bodyPanel.contentView?.frame.size == bodyPanel.frame.size
                && hubPanel.contentView?.frame.size == hubPanel.frame.size
                && selectedScreen.visibleFrame.contains(hubPanel.frame)
            measurements.append([
                "scale": scale, "passed": valid,
                "bodyWidth": bodyPanel.frame.width, "expectedBodyWidth": expectedWidth,
                "hubWidth": hubPanel.frame.width, "hubHeight": hubPanel.frame.height,
                "bodyHeight": bodyPanel.frame.height,
                "bodyHostWidth": bodyPanel.contentView?.frame.width ?? 0,
                "bodyHostHeight": bodyPanel.contentView?.frame.height ?? 0,
                "hubHostWidth": hubPanel.contentView?.frame.width ?? 0,
                "rightEdge": bodyPanel.frame.maxX,
                "screenRightEdge": selectedScreen.visibleFrame.maxX,
            ])
            try? PanelEvidenceCapture.capture(panels: visibleEvidencePanels,
                directory: URL(fileURLWithPath: directory), name: "live-scale-\(index)-\(Int(scale * 100))")
        }
        presentation.setVisibilityMode(.fold)
        closeHub()
        hover.dismiss()
        presentation.pointerExitedWidget(hover: hover)
        try? await Task.sleep(nanoseconds: 500_000_000)
        let folded = abs(bodyPanel.frame.width - ceil(CompanionLayout(uiScale: presentation.uiScale).pillHotZone)) < 0.5
        measurements.append(["folded": true, "passed": folded])
        try? PanelEvidenceCapture.capture(panels: visibleEvidencePanels,
            directory: URL(fileURLWithPath: directory), name: "live-folded")
        hideWidget()
        try? await Task.sleep(nanoseconds: 300_000_000)
        measurements.append(["hidden": true, "passed": !bodyPanel.isVisible && !hubPanel.isVisible
                             && !tooltipPanel.isVisible && !settingsHandlePanel.isVisible])
        showHub(.home)
        try? await Task.sleep(nanoseconds: 300_000_000)
        measurements.append(["reopened": true, "passed": hubPanel.isVisible && hubPanel.canBecomeKey])
        try? PanelEvidenceCapture.capture(panels: visibleEvidencePanels,
            directory: URL(fileURLWithPath: directory), name: "hub-home")
        if let data = try? JSONSerialization.data(withJSONObject: measurements, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: directory).appendingPathComponent("live-layout-audit.json"))
        }
    }

    private func auditSurfaceMotion(directory: String) async {
        let savedScale = presentation.uiScale
        let savedVisibility = presentation.visibilityMode
        defer {
            presentation.setUIScale(savedScale)
            presentation.setVisibilityMode(savedVisibility)
        }
        presentation.setUIScale(1)
        presentation.setVisibilityMode(.fold)
        closeHub()
        hover.dismiss()
        try? await Task.sleep(nanoseconds: 650_000_000)
        presentation.setVisibilityMode(.always)
        var samples: [[String: Any]] = []
        for index in 0..<20 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            samples.append(["step": index, "alpha": bodyPanel.alphaValue,
                            "height": bodyPanel.frame.height, "expanded": bodyTransition.state.expanded])
        }
        let hasIntermediateOpacity = samples.contains {
            guard let alpha = $0["alpha"] as? CGFloat else { return false }
            return alpha > 0.01 && alpha < 0.99
        }
        let snapshot = presentation.snapshot(for: connection)
        let layout = CompanionLayout(uiScale: 1)
        let compactHeight = ceil(layout.shapeHeight(metrics: snapshot.metrics))
        let compactPassed = bodyPanel.frame.height == compactHeight
            && snapshot.metrics.allSatisfy { !$0.showsHeadline }
        try? PanelEvidenceCapture.capture(panels: visibleEvidencePanels,
            directory: URL(fileURLWithPath: directory), name: "compact-no-placeholders")

        // Interrupt a fold and then interrupt a hide before its completion.
        presentation.setVisibilityMode(.fold)
        try? await Task.sleep(nanoseconds: 40_000_000)
        presentation.setVisibilityMode(.always)
        try? await Task.sleep(nanoseconds: 350_000_000)
        hideWidget()
        try? await Task.sleep(nanoseconds: 70_000_000)
        showHub()
        try? await Task.sleep(nanoseconds: 400_000_000)
        let reversalPassed = bodyPanel.isVisible && bodyPanel.alphaValue == 1
            && bodyTransition.state.expanded && hubPanel.isVisible && hubPanel.alphaValue == 1
            && bodyPanel.contentView?.frame.size == bodyPanel.frame.size
        let report: [String: Any] = [
            "opacitySamples": samples, "smoothOpacityPassed": hasIntermediateOpacity,
            "compactHeight": compactHeight, "compactLayoutPassed": compactPassed,
            "rapidReversalPassed": reversalPassed,
            "passed": hasIntermediateOpacity && compactPassed && reversalPassed,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: directory).appendingPathComponent("motion-audit.json"))
        }
    }
}
