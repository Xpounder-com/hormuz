import Foundation

/// Combine's @Published emits before storage changes. Coalesce those notifications
/// onto the next main-queue turn before reading any layout state.
@MainActor
public final class SettledPanelUpdate {
    private var pending: DispatchWorkItem?
    public init() {}

    public func schedule(_ update: @escaping @MainActor () -> Void) {
        guard pending == nil else { return }
        let item = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.pending = nil
            update()
        }
        pending = item
        DispatchQueue.main.async(execute: item)
    }

    public func cancel() {
        pending?.cancel()
        pending = nil
    }
}
