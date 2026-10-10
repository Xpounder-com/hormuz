import Foundation

public protocol NativeAppearanceStore { func removeObject(forKey: String) }
extension UserDefaults: NativeAppearanceStore {}

public enum NativeAppearanceSettings {
    public static let keys = ["companion.visibility", "companion.uiScale", "companion.display"]
    public static func reset(_ defaults: any NativeAppearanceStore) {
        for key in keys { defaults.removeObject(forKey: key) }
    }
}
