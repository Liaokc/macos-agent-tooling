import SwiftUI

// ─────────────────────────────────────────────────────────────────
// SettingsView — Agent configuration panel
// ─────────────────────────────────────────────────────────────────

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var config: AgentBridge.AgentConfig = AgentBridge.AgentConfig()
    @State private var availableModels: [ModelInfo] = []
    @State private var isSaving: Bool = false
    @State private var showResetAlert: Bool = false
    @State private var loadError: String?

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Settings")
                    .font(.headline)
                Spacer()
                Button("Done") { dismiss() }
            }
            .padding()

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    // ── Model ──────────────────────────────────────────────
                    settingsSection("Model") {
                        Picker("Model", selection: $config.model) {
                            ForEach(availableModels) { m in
                                Text(m.name).tag(m.name)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(maxWidth: 300)

                        Button("Refresh Models") {
                            Task { await loadModels() }
                        }
                    }

                    // ── Agent Behavior ─────────────────────────────────────
                    settingsSection("Agent Behavior") {
                        sliderRow(
                            "Max Iterations",
                            value: Binding(
                                get: { Double(config.maxIterations) },
                                set: { config.maxIterations = Int($0) }
                            ),
                            range: 1...30, step: 1,
                            display: "\(config.maxIterations)"
                        )

                        sliderRow(
                            "Temperature",
                            value: $config.temperature,
                            range: 0...1.5, step: 0.1,
                            display: String(format: "%.1f", config.temperature)
                        )

                        Toggle("Show Agent Thinking", isOn: $config.showThinking)
                        Toggle("Confirm Dangerous Tools", isOn: $config.toolConfirmation)
                    }

                    // ── Memory ─────────────────────────────────────────────
                    settingsSection("Memory") {
                        Toggle("Enable Semantic Memory", isOn: $config.memorySemanticEnabled)
                        Toggle("Enable Episodic Memory", isOn: $config.memoryEpisodicEnabled)

                        sliderRow(
                            "Prune After (days)",
                            value: Binding(
                                get: { Double(config.memoryPruneDays) },
                                set: { config.memoryPruneDays = Int($0) }
                            ),
                            range: 7...365, step: 7,
                            display: "\(config.memoryPruneDays)d"
                        )

                        NavigationLink("View/Manage Memories") {
                            MemoryManagerView()
                        }
                    }

                    // ── System Prompt ──────────────────────────────────────
                    settingsSection("System Prompt") {
                        TextEditor(text: Binding(
                            get: { config.systemPrompt ?? "" },
                            set: { config.systemPrompt = $0.isEmpty ? nil : $0 }
                        ))
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 150)
                        .scrollContentBackground(.hidden)
                        .background(Color(nsColor: .textBackgroundColor))
                        .cornerRadius(6)
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(Color.secondary.opacity(0.2), lineWidth: 1)
                        )

                        Button("Reset to Default") {
                            showResetAlert = true
                        }
                    }

                    // ── Sandbox ─────────────────────────────────────────────
                    settingsSection("Sandbox") {
                        HStack {
                            Text("Workspace:")
                                .foregroundColor(.secondary)
                            TextField("path", text: $config.sandboxWorkspace)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 300)
                        }
                    }

                    if let err = loadError {
                        Text(err)
                            .font(.caption)
                            .foregroundColor(.red)
                    }
                }
                .padding()
            }

            Divider()

            // Save Button
            HStack {
                Spacer()
                Button(isSaving ? "Saving..." : "Save Changes") {
                    Task { await saveConfig() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isSaving || config.model.isEmpty)
            }
            .padding()
        }
        .frame(width: 600, height: 700)
        .task {
            await loadConfig()
            await loadModels()
        }
        .alert("Reset System Prompt?", isPresented: $showResetAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Reset") { config.systemPrompt = nil }
        }
    }

    // ─── Section Helper ───────────────────────────────────────────────────────

    @ViewBuilder
    private func settingsSection<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.subheadline.bold())
                .foregroundColor(.secondary)
            content()
        }
    }

    // ─── Slider Row ───────────────────────────────────────────────────────────

    private func sliderRow(
        _ label: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        step: Double,
        display: String
    ) -> some View {
        HStack {
            Text(label)
            Slider(value: value, in: range, step: step)
            Text(display)
                .frame(width: 40)
                .foregroundColor(.secondary)
        }
    }

    // ─── Data Loading ─────────────────────────────────────────────────────────

    private func loadConfig() async {
        do {
            config = try await AgentBridge.shared.getConfig()
            loadError = nil
        } catch {
            loadError = "Failed to load config: \(error.localizedDescription)"
        }
    }

    private func loadModels() async {
        do {
            availableModels = try await AgentBridge.shared.listModels()
        } catch {
            // silently fail — models list is optional
        }
    }

    private func saveConfig() async {
        isSaving = true
        do {
            config = try await AgentBridge.shared.updateConfig(config)
            loadError = nil
        } catch {
            loadError = "Failed to save: \(error.localizedDescription)"
        }
        isSaving = false
    }
}
