import Combine
import Foundation

@MainActor
public final class HoverCoordinator: ObservableObject {
    @Published public private(set) var selectedMetric: CompanionMetricID?
    @Published public private(set) var pinnedMetric: CompanionMetricID?
    @Published public private(set) var isPointerInsideTooltip = false

    private let dismissalDelayNanoseconds: UInt64
    private var dismissalTask: Task<Void, Never>?

    public init(dismissalDelayNanoseconds: UInt64 = 250_000_000) {
        self.dismissalDelayNanoseconds = dismissalDelayNanoseconds
    }

    public func enterMetric(_ metric: CompanionMetricID) {
        cancelPendingDismissal()
        guard pinnedMetric == nil else { return }
        selectedMetric = metric
    }

    public func leaveMetric(_ metric: CompanionMetricID) {
        guard selectedMetric == metric, pinnedMetric == nil else { return }
        scheduleDismissal()
    }

    public func enterTooltip() {
        isPointerInsideTooltip = true
        cancelPendingDismissal()
    }

    public func leaveTooltip() {
        isPointerInsideTooltip = false
        guard pinnedMetric == nil else { return }
        scheduleDismissal()
    }

    public func togglePin(_ metric: CompanionMetricID) {
        cancelPendingDismissal()
        selectedMetric = metric
        pinnedMetric = pinnedMetric == metric ? nil : metric
    }

    public func showAndPin(_ metric: CompanionMetricID) {
        cancelPendingDismissal()
        selectedMetric = metric
        pinnedMetric = metric
    }

    public func dismiss() {
        cancelPendingDismissal()
        selectedMetric = nil
        pinnedMetric = nil
        isPointerInsideTooltip = false
    }

    public func cancelPendingDismissal() {
        dismissalTask?.cancel()
        dismissalTask = nil
    }

    private func scheduleDismissal() {
        cancelPendingDismissal()
        dismissalTask = Task { [weak self, dismissalDelayNanoseconds] in
            try? await Task.sleep(nanoseconds: dismissalDelayNanoseconds)
            guard !Task.isCancelled, let self else { return }
            guard self.pinnedMetric == nil, !self.isPointerInsideTooltip else { return }
            self.selectedMetric = nil
            self.dismissalTask = nil
        }
    }
}
