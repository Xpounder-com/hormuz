import Foundation
import SwiftUI

// The same executable serves the GUI and helper. The helper never starts AppKit
// or opens a browser and emits one access credential only on successful stdout.
let hormuzArguments = Array(CommandLine.arguments.dropFirst())
let isCompanionLaunch = hormuzArguments.contains { $0.hasPrefix("--companion-") }
if !hormuzArguments.isEmpty && !isCompanionLaunch {
    CredentialCommand.run(arguments: hormuzArguments)
} else {
    HormuzApp.main()
}
