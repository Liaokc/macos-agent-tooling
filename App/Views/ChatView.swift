import SwiftUI

struct ChatView: View {
    @StateObject private var viewModel = ChatViewModel()
    @State private var sessionPickerPresented = false
    @State private var modelPickerPresented = false
    @State private var sessionListPresented = false

    var body: some View {
        VStack(spacing: 0) {
            // ── Toolbar ──────────────────────────────────────────
            toolbar

            Divider()

            // ── Messages ─────────────────────────────────────────
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(viewModel.messages) { msg in
                            MessageBubbleView(message: msg)
                                .id(msg.id)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                }
                .onChange(of: viewModel.messages.count) { _, _ in
                    if let lastId = viewModel.messages.last?.id {
                        withAnimation {
                            proxy.scrollTo(lastId, anchor: .bottom)
                        }
                    }
                }
            }

            // ── Stats bar ─────────────────────────────────────────
            if let stats = viewModel.hardwareStats {
                StatsBarView(stats: stats)
            }

            Divider()

            // ── Error ─────────────────────────────────────────────
            if let err = viewModel.errorMessage {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                    Text(err)
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                    Button("Dismiss") {
                        viewModel.errorMessage = nil
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 6)
                .background(Color.orange.opacity(0.1))
            }

            // ── Input ─────────────────────────────────────────────
            inputArea
        }
        .frame(minWidth: 600, minHeight: 500)
    }

    // ─────────────────────────────────────────────────────────────
    // Toolbar
    // ─────────────────────────────────────────────────────────────

    private var toolbar: some View {
        HStack(spacing: 12) {
            // Model Picker
            Menu {
                ForEach(viewModel.availableModels) { model in
                    Button {
                        viewModel.selectedModel = model.name
                    } label: {
                        HStack {
                            Text(model.name)
                            if model.name == viewModel.selectedModel {
                                Image(systemName: "checkmark")
                            }
                        }
                    }
                }
                Divider()
                Button("Refresh Models") {
                    Task { await viewModel.loadModels() }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "cpu")
                    Text(viewModel.selectedModel.isEmpty ? "Select Model" : viewModel.selectedModel)
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.accentColor.opacity(0.12))
                .cornerRadius(8)
            }
            .menuStyle(.borderlessButton)
            .frame(maxWidth: 200)

            // Connection indicator
            Circle()
                .fill(viewModel.isConnected ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            Text(viewModel.isConnected ? "Ollama Connected" : "Ollama Offline")
                .font(.caption)
                .foregroundColor(.secondary)

            Spacer()

            // Session Picker
            Menu {
                ForEach(viewModel.sessions) { session in
                    Button {
                        Task { await viewModel.switchSession(session) }
                    } label: {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(session.title)
                                    .font(.body)
                                Text(session.model)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                            if session.id == viewModel.currentSession?.id {
                                Image(systemName: "checkmark")
                            }
                        }
                    }
                }
                Divider()
                Button("New Chat") {
                    Task { await viewModel.createSession() }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "bubble.left.and.bubble.right")
                    Text(viewModel.currentSession?.title ?? "Sessions")
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.secondary.opacity(0.12))
                .cornerRadius(8)
            }
            .menuStyle(.borderlessButton)

            Button {
                Task { await viewModel.createSession() }
            } label: {
                Image(systemName: "plus.circle")
                    .font(.title3)
            }
            .buttonStyle(.plain)
            .help("New Chat")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    // ─────────────────────────────────────────────────────────────
    // Input Area
    // ─────────────────────────────────────────────────────────────

    private var inputArea: some View {
        HStack(alignment: .bottom, spacing: 12) {
            TextField("Type a message...", text: $viewModel.inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.body)
                .lineLimit(1...8)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.textFieldColor)
                .cornerRadius(12)
                .onSubmit {
                    Task { await viewModel.sendMessage() }
                }

            Button {
                Task { await viewModel.sendMessage() }
            } label: {
                Group {
                    if viewModel.isLoading {
                        ProgressView()
                            .scaleEffect(0.7)
                    } else {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                }
                .frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .disabled(viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isLoading)
            .keyboardShortcut(.return, modifiers: [])
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

// ─────────────────────────────────────────────────────────────────
// Message Bubble
// ─────────────────────────────────────────────────────────────────

struct MessageBubbleView: View {
    let message: MessageItem

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .user {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                Text(message.role == .user ? "You" : "Assistant")
                    .font(.caption2)
                    .foregroundColor(.secondary)

                Text(message.content)
                    .font(.body)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(message.role == .user ? Color.accentColor : Color.secondary.opacity(0.15))
                    .foregroundColor(message.role == .user ? .white : .primary)
                    .cornerRadius(16)

                if message.isStreaming {
                    Text("...")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            if message.role == .assistant {
                Spacer(minLength: 60)
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// Stats Bar
// ─────────────────────────────────────────────────────────────────

struct StatsBarView: View {
    let stats: HardwareStats

    var body: some View {
        HStack(spacing: 16) {
            Label {
                Text(String(format: "CPU %.0f%%", stats.cpuPercent))
            } icon: {
                Image(systemName: "cpu")
            }
            .font(.caption2)

            Label {
                Text(String(format: "RAM %.0f%%", stats.memoryPercent))
            } icon: {
                Image(systemName: "memorychip")
            }
            .font(.caption2)

            if !stats.gpuStats.isEmpty {
                Label {
                    if let util = stats.gpuStats.first?["utilization_percent"] {
                        Text(String(format: "GPU %.0f%%", util))
                    }
                } icon: {
                    Image(systemName: "square.stack.3d.up")
                }
                .font(.caption2)
            }

            Spacer()
        }
        .foregroundColor(.secondary)
        .padding(.horizontal, 16)
        .padding(.vertical, 4)
    }
}

// ─────────────────────────────────────────────────────────────────
// Color Extension
// ─────────────────────────────────────────────────────────────────

extension Color {
    static let textFieldColor = Color(nsColor: .textBackgroundColor)
}

// ─────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────

#Preview {
    ChatView()
}
