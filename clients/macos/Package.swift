// swift-tools-version: 5.10
import PackageDescription
import Foundation

// The bridge is a source-matched static archive, not a downloaded binary.
// App build/packaging workflows always supply this path and assert its ABI
// symbol. Omitting it supports isolated legacy Swift contract fixtures only.
let rustUI = ProcessInfo.processInfo.environment["HORMUZ_RUST_UI_LIBRARY_DIR"]

var package = Package(
    name: "HormuzMac",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Hormuz", targets: ["Hormuz"]),
        .library(name: "HormuzClientCore", targets: ["HormuzClientCore"]),
    ],
    targets: [
        .target(name: "HormuzClientCore"),
        .executableTarget(name: "Hormuz", dependencies: ["HormuzClientCore"]),
        .testTarget(name: "HormuzClientCoreTests", dependencies: ["HormuzClientCore"]),
        // Provider-free verification tool. Never copied into the app bundle.
        .executableTarget(name: "HormuzFixtureProbe", dependencies: ["HormuzClientCore"], path: "Tests/HormuzFixtureProbe"),
    ]
)

if let rustUI {
    package.products.append(.library(name: "HormuzRustBridge", targets: ["HormuzRustBridge"]))
    package.targets.append(.target(name: "CHormuzRust", publicHeadersPath: "include"))
    package.targets.append(.target(name: "HormuzRustBridge", dependencies: ["CHormuzRust", "HormuzClientCore"],
        linkerSettings: [.unsafeFlags(["-L", rustUI, "-lhormuz_client_ui"])]))
    package.targets.append(.testTarget(name: "HormuzRustBridgeTests", dependencies: ["HormuzRustBridge", "CHormuzRust"]))
    package.targets.first(where: { $0.name == "Hormuz" })?.dependencies.append(.target(name: "HormuzRustBridge"))
}
