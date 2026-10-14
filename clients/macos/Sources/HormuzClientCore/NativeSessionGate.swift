/// Polling gate only, not an authentication or lock-screen security boundary.
/// A console/login signal alone must never override a locked or unknown screen.
public enum NativeSessionGate {
    public static func isBlocked(onConsole: Bool?, loginDone: Bool?, screenLocked: Bool?) -> Bool {
        onConsole != true || loginDone != true || screenLocked != false
    }
}
