// swift-tools-version: 5.10
import PackageDescription

let package = Package(
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
