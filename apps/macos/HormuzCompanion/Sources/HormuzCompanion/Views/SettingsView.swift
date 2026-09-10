import HormuzCompanionCore
import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: CompanionStore
    let displayOptions: [DisplayOption]
    let showWidget: () -> Void
    let quit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Hormuz Companion")
                    .font(.title2.weight(.semibold))
                Text("Demo data — not connected to a gateway")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }

            Form {
                Picker("Visibility", selection: visibilityBinding) {
                    ForEach(CompanionVisibilityMode.allCases) { mode in
                        Text(mode.displayName).tag(mode)
                    }
                }

                Picker("UI scale", selection: scaleBinding) {
                    Text("100%").tag(1.0)
                    Text("125%").tag(1.25)
                    Text("150%").tag(1.5)
                }

                Picker("Scenario", selection: scenarioBinding) {
                    ForEach(DemoScenario.allCases) { scenario in
                        Text(scenario.displayName).tag(scenario)
                    }
                }

                Picker("Display", selection: displayBinding) {
                    Text("Primary display").tag("primary")
                    ForEach(displayOptions) { option in
                        Text(option.name).tag(option.id)
                    }
                }
            }
            .formStyle(.grouped)

            HStack {
                Button("Show Widget", action: showWidget)
                    .keyboardShortcut("s", modifiers: [.command, .shift])
                Spacer()
                Button("Quit Hormuz Demo", action: quit)
            }
        }
        .padding(24)
        .frame(width: 440, height: 330)
    }

    private var visibilityBinding: Binding<CompanionVisibilityMode> {
        Binding(get: { store.visibilityMode }, set: store.setVisibilityMode)
    }

    private var scaleBinding: Binding<Double> {
        Binding(get: { store.uiScale }, set: store.setUIScale)
    }

    private var scenarioBinding: Binding<DemoScenario> {
        Binding(get: { store.scenario }, set: store.setScenario)
    }

    private var displayBinding: Binding<String> {
        Binding(get: { store.selectedDisplayID }, set: store.setSelectedDisplayID)
    }
}
