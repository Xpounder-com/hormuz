import AppKit
import Combine

@MainActor
final class EdgeSurfaceState: ObservableObject {
    @Published var expanded = false
}

/// Fade the existing surface out before changing its geometry, then reveal the
/// new one. Scale changes still settle atomically; no old completion can resize
/// a newer surface. One cancellable task handles rapid pointer reversals.
@MainActor
final class EdgeSurfaceTransition {
    let state = EdgeSurfaceState()
    private let panel: NSPanel
    private var task: Task<Void, Never>?
    private var targetFrame: NSRect?
    private var targetExpanded: Bool?
    private var showing = false

    init(panel: NSPanel) { self.panel = panel }

    func show(frame: NSRect, expanded: Bool) {
        guard !showing || targetFrame != frame || targetExpanded != expanded else { return }
        let shouldFade = panel.isVisible && state.expanded != expanded
        let wasVisible = panel.isVisible
        task?.cancel()
        targetFrame = frame
        targetExpanded = expanded
        showing = true
        task = Task { [weak self] in
            guard let self else { return }
            if shouldFade {
                await fade(panel, to: 0, duration: 0.08)
                guard !Task.isCancelled else { return }
            }
            state.expanded = expanded
            panel.setFrame(frame, display: false)
            panel.contentView?.frame = NSRect(origin: .zero, size: panel.frame.size)
            panel.contentView?.needsLayout = true
            panel.contentView?.layoutSubtreeIfNeeded()
            if !wasVisible || shouldFade { panel.alphaValue = 0 }
            panel.ignoresMouseEvents = false
            panel.orderFrontRegardless()
            // Let SwiftUI lay out the new surface while it is transparent.
            await Task.yield()
            guard !Task.isCancelled else { return }
            await fade(panel, to: 1, duration: shouldFade || !wasVisible || panel.alphaValue < 1 ? 0.20 : 0)
        }
    }

    func hide(immediate: Bool = false) {
        guard showing || immediate else { return }
        showing = false
        task?.cancel()
        panel.ignoresMouseEvents = true
        if immediate { panel.orderOut(nil); return }
        task = Task { [weak self] in
            guard let self else { return }
            await fade(panel, to: 0, duration: 0.16)
            guard !Task.isCancelled else { return }
            panel.orderOut(nil)
        }
    }
}

@MainActor
final class PanelVisibilityTransition {
    private let panel: NSPanel
    private var showing = false
    private var task: Task<Void, Never>?
    init(panel: NSPanel) { self.panel = panel }

    func setVisible(_ visible: Bool, immediate: Bool = false) {
        guard showing != visible || immediate else { return }
        showing = visible
        task?.cancel()
        if immediate {
            panel.alphaValue = visible ? 1 : 0
            visible ? panel.orderFrontRegardless() : panel.orderOut(nil)
            return
        }
        panel.ignoresMouseEvents = !visible
        if visible {
            if !panel.isVisible { panel.alphaValue = 0 }
            panel.orderFrontRegardless()
        }
        task = Task { [weak self] in
            guard let self else { return }
            await fade(panel, to: visible ? 1 : 0, duration: visible ? 0.20 : 0.16)
            guard !Task.isCancelled else { return }
            if !visible { panel.orderOut(nil) }
        }
    }
}

@MainActor
private func fade(_ panel: NSPanel, to target: CGFloat, duration: TimeInterval) async {
    let duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? min(duration, 0.08) : duration
    guard duration > 0 else { panel.alphaValue = target; return }
    let start = panel.alphaValue
    let clock = ContinuousClock()
    let began = clock.now
    while !Task.isCancelled {
        let elapsed = began.duration(to: clock.now).components
        let seconds = Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18
        let progress = min(1, seconds / duration)
        let eased = progress * progress * (3 - 2 * progress)
        panel.alphaValue = start + (target - start) * eased
        if progress >= 1 { return }
        try? await Task.sleep(nanoseconds: 16_000_000)
    }
}
