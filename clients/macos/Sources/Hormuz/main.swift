import Foundation
import SwiftUI

// The same executable serves the GUI and helper. The helper never starts AppKit
// or opens a browser and emits one access credential only on successful stdout.
if CommandLine.arguments.count > 1 {
    CredentialCommand.run(arguments: Array(CommandLine.arguments.dropFirst()))
} else {
    HormuzApp.main()
}
