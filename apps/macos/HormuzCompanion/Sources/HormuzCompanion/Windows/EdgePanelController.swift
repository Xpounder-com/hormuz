import AppKit
import Combine
import HormuzCompanionCore
import SwiftUI

struct DisplayOption: Identifiable, Hashable {
    let id: String
    let name: String
}

private final class OverlayPanel: NSPanel {
    init() {
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

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class EdgePanelController {
    let store: CompanionStore
    let hover: HoverCoordinator

    private let bodyPanel = OverlayPanel()
    private let tooltipPanel = OverlayPanel()
    private let settingsHandlePanel = OverlayPanel()
    private var settingsWindowController: SettingsWindowController?
    private var cancellables = Set<AnyCancellable>()
    private var screenObserver: NSObjectProtocol?
    private var globalMouseMonitor: Any?
    private var localMouseMonitor: Any?

    init(store: CompanionStore, hover: HoverCoordinator) {
        self.store = store
        self.hover = hover
    }

    func start() {
        installContent()
        installObservers()
        updatePanels(animated: false)

        if let metric = store.initialDetailMetric {
            store.ensureWidgetVisible()
            hover.showAndPin(metric)
        }

        if store.showSettingsOnLaunch {
            showSettings()
        }

        if let captureDirectory = store.captureDirectory {
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 800_000_000)
                guard let self else { return }
                do {
                    try EvidenceCapture.capture(
                        panels: self.visibleEvidencePanels,
                        directory: URL(fileURLWithPath: captureDirectory, isDirectory: true),
                        name: self.store.captureName
                    )
                } catch {
                    NSLog("Hormuz Companion evidence capture failed: %@", error.localizedDescription)
                }
            }
        }

        if let motionPath = store.motionRecordingPath {
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 800_000_000)
                guard let self else { return }
                do {
                    try await MotionEvidenceRecorder.record(
                        to: URL(fileURLWithPath: motionPath),
                        store: self.store,
                        hover: self.hover,
                        panels: { [weak self] in self?.visibleEvidencePanels ?? [] }
                    )
                } catch {
                    NSLog("Hormuz Companion motion capture failed: %@", error.localizedDescription)
                }
            }
        }
    }

    func stop() {
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        if let globalMouseMonitor { NSEvent.removeMonitor(globalMouseMonitor) }
        if let localMouseMonitor { NSEvent.removeMonitor(localMouseMonitor) }
        screenObserver = nil
        globalMouseMonitor = nil
        localMouseMonitor = nil
        bodyPanel.orderOut(nil)
        tooltipPanel.orderOut(nil)
        settingsHandlePanel.orderOut(nil)
    }

    func showWidget() {
        store.ensureWidgetVisible()
        updatePanels(animated: true)
    }

    func hideWidget() {
        hover.dismiss()
        store.hideWidget()
    }

    func showAndPin(_ metric: CompanionMetricID) {
        store.ensureWidgetVisible()
        hover.showAndPin(metric)
    }

    func showSettings() {
        if settingsWindowController == nil {
            settingsWindowController = SettingsWindowController(
                store: store,
                displayOptions: displayOptions,
                showWidget: { [weak self] in self?.showWidget() },
                quit: { NSApplication.shared.terminate(nil) }
            )
        } else {
            settingsWindowController?.updateDisplayOptions(displayOptions)
        }
        settingsWindowController?.show()
    }

    private func installContent() {
        bodyPanel.contentView = hostingView(
            EdgeNotchView(
                store: store,
                hover: hover,
                openSettings: { [weak self] in self?.showSettings() },
                refreshDemo: { [weak self] in self?.store.refreshDemo() },
                hideWidget: { [weak self] in self?.hideWidget() },
                quit: { NSApplication.shared.terminate(nil) }
            )
        )

        tooltipPanel.contentView = hostingView(
            UsageTooltip(
                store: store,
                hover: hover,
                openSettings: { [weak self] in self?.showSettings() }
            )
        )

        settingsHandlePanel.contentView = hostingView(
            SettingsHandle(
                store: store,
                openSettings: { [weak self] in self?.showSettings() }
            )
        )
    }

    private func hostingView<Content: View>(_ content: Content) -> NSHostingView<Content> {
        let view = NSHostingView(rootView: content)
        view.autoresizingMask = [.width, .height]
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.clear.cgColor
        return view
    }

    private func installObservers() {
        store.$uiScale.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        store.$visibilityMode.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        store.$widgetVisible.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        store.$transientExpanded.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        store.$selectedDisplayID.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: false) }
            .store(in: &cancellables)
        store.$snapshot.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        hover.$selectedMetric.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)
        hover.$pinnedMetric.dropFirst().sink { [weak self] _ in self?.updatePanels(animated: true) }
            .store(in: &cancellables)

        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.updatePanels(animated: false) }
        }

        globalMouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) {
            [weak self] _ in
            Task { @MainActor in self?.dismissPinnedDetailIfClickIsOutside() }
        }
        localMouseMonitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) {
            [weak self] event in
            self?.dismissPinnedDetailIfClickIsOutside()
            return event
        }
    }

    private func dismissPinnedDetailIfClickIsOutside() {
        guard hover.pinnedMetric != nil else { return }
        let point = NSEvent.mouseLocation
        let interactiveFrames = [bodyPanel, tooltipPanel, settingsHandlePanel]
            .filter(\.isVisible)
            .map(\.frame)
        guard !interactiveFrames.contains(where: { $0.contains(point) }) else { return }
        if settingsWindowController?.window?.frame.contains(point) == true { return }
        hover.dismiss()
    }

    private func updatePanels(animated: Bool) {
        guard store.widgetVisible else {
            bodyPanel.orderOut(nil)
            tooltipPanel.orderOut(nil)
            settingsHandlePanel.orderOut(nil)
            return
        }

        let layout = CompanionLayout(uiScale: store.uiScale)
        let screen = selectedScreen
        let visibleFrame = screen.visibleFrame
        let expanded = store.shouldExpand(hover: hover)
        let bodyFrame = makeBodyFrame(
            layout: layout,
            visibleFrame: visibleFrame,
            expanded: expanded
        )

        setFrame(bodyFrame, for: bodyPanel, animated: animated)
        bodyPanel.orderFrontRegardless()

        if expanded {
            let orbFrame = makeSettingsHandleFrame(layout: layout, bodyFrame: bodyFrame)
            setFrame(orbFrame, for: settingsHandlePanel, animated: animated)
            settingsHandlePanel.orderFrontRegardless()
        } else {
            settingsHandlePanel.orderOut(nil)
        }

        guard expanded, let metric = hover.selectedMetric,
              let index = store.snapshot.metrics.firstIndex(where: { $0.id == metric }),
              let reading = store.snapshot.metric(metric)
        else {
            tooltipPanel.orderOut(nil)
            return
        }

        let tooltipFrame = makeTooltipFrame(
            layout: layout,
            bodyFrame: bodyFrame,
            visibleFrame: visibleFrame,
            metricIndex: index,
            reading: reading
        )
        setFrame(tooltipFrame, for: tooltipPanel, animated: animated)
        tooltipPanel.orderFrontRegardless()
    }

    private func setFrame(_ frame: NSRect, for panel: NSPanel, animated: Bool) {
        synchronizeContentView(of: panel, to: frame.size)
        guard animated, panel.isVisible else {
            panel.setFrame(frame, display: true)
            synchronizeContentView(of: panel, to: frame.size)
            return
        }
        NSAnimationContext.runAnimationGroup(
            { context in
                context.duration = 0.32
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrame(frame, display: true)
            },
            completionHandler: { [weak panel] in
                Task { @MainActor in
                    guard let panel else { return }
                    self.synchronizeContentView(of: panel, to: frame.size)
                }
            }
        )
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
        expanded: Bool
    ) -> NSRect {
        let width = expanded ? layout.sideBodyDepth : layout.pillHotZone
        let height = expanded
            ? layout.shapeHeight(metricCount: store.snapshot.metrics.count)
            : layout.pillHeight
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
        reading: CompanionMetricReading
    ) -> NSRect {
        let ringCenterY = bodyFrame.maxY - layout.ringCenterFromTop(index: metricIndex)
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
        let center = CGPoint(
            x: bodyFrame.maxX - layout.curlRadius,
            y: bodyFrame.minY
        )
        return NSRect(
            x: center.x - layout.orbHotZone / 2,
            y: center.y - layout.orbHotZone / 2,
            width: layout.orbHotZone,
            height: layout.orbHotZone
        )
    }

    private var selectedScreen: NSScreen {
        if store.selectedDisplayID != "primary",
           let match = NSScreen.screens.first(where: { Self.identifier(for: $0) == store.selectedDisplayID }) {
            return match
        }
        return NSScreen.screens.first ?? NSScreen.main ?? NSScreen()
    }

    private var displayOptions: [DisplayOption] {
        NSScreen.screens.enumerated().map { index, screen in
            DisplayOption(
                id: Self.identifier(for: screen),
                name: "Display \(index + 1) · \(Int(screen.frame.width))×\(Int(screen.frame.height))"
            )
        }
    }

    private var visibleEvidencePanels: [(name: String, panel: NSPanel)] {
        [
            ("body", bodyPanel),
            ("tooltip", tooltipPanel),
            ("settings-handle", settingsHandlePanel)
        ].filter { $0.panel.isVisible }
    }

    private static func identifier(for screen: NSScreen) -> String {
        if let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber {
            return number.stringValue
        }
        return "screen-\(Int(screen.frame.origin.x))-\(Int(screen.frame.origin.y))"
    }
}
