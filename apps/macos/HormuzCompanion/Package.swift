// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "HormuzCompanion",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "HormuzCompanionCore", targets: ["HormuzCompanionCore"]),
        .executable(name: "HormuzCompanion", targets: ["HormuzCompanion"])
    ],
    targets: [
        .target(name: "HormuzCompanionCore"),
        .executableTarget(
            name: "HormuzCompanion",
            dependencies: ["HormuzCompanionCore"]
        ),
        .testTarget(
            name: "HormuzCompanionCoreTests",
            dependencies: ["HormuzCompanionCore"]
        )
    ],
    swiftLanguageModes: [.v5]
)
