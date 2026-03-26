import SwiftUI

// ─────────────────────────────────────────────────────────────────
// AgentModeOverlay — floating activity panel for agent execution
// ─────────────────────────────────────────────────────────────────

struct AgentModeOverlay: View {
    @ObservedObject var viewModel: AgentModeViewModel
    let isExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                Image(systemName: "cpu")
                    .font(.caption)
                    .foregroundColor(.accentColor)
                Text("Agent Activity")
                    .font(.caption.bold())
                Spacer()
                if viewModel.activities.count > 0 {
                    IterationProgressBar(
                        current: viewModel.currentIteration,
                        max: viewModel.maxIterations
                    )
                }
                if viewModel.isActive {
                    ProgressView()
                        .scaleEffect(0.5)
                        .frame(width: 12, height: 12)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            // Activity List
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        ForEach(viewModel.activities) { item in
                            activityView(for: item)
                                .id(item.id)
                        }
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                }
                .onChange(of: viewModel.activities.count) { _, _ in
                    if let last = viewModel.activities.last {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }
        }
        .frame(minHeight: isExpanded ? 300 : 0)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(10)
        .shadow(color: .black.opacity(0.12), radius: 8, y: 2)
    }

    @ViewBuilder
    private func activityView(for item: AgentActivityItem) -> some View {
        switch item {
        case .thinking(_, let text):
            AgentThinkingBubble(text: text)

        case .iterationStart(_, let number):
            HStack(spacing: 4) {
                Image(systemName: "arrow.clockwise")
                    .font(.caption2)
                Text("Iteration \(number)")
                    .font(.caption2.bold())
            }
            .foregroundColor(.secondary)
            .padding(.top, 4)

        case .toolCall(_, let tool, let args, _):
            ToolCallCard(
                tool: tool,
                args: args,
                state: .running,
                durationMs: nil,
                output: nil
            )

        case .toolResult(_, let tool, let output, let success, let durationMs):
            ToolCallCard(
                tool: tool,
                args: [:],
                state: success ? .done : .error,
                durationMs: durationMs,
                output: output
            )

        case .textChunk(_, let text):
            Text(text)
                .font(.caption)
                .foregroundColor(.secondary)

        case .done(_, let finalText):
            HStack(spacing: 4) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("Done")
                    .font(.caption.bold())
                if !finalText.isEmpty {
                    Text("- \(String(finalText.prefix(60)))...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.top, 4)

        case .error(_, let message):
            HStack(spacing: 4) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
                Text(message)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// ToolCallCard — single tool invocation card
// ─────────────────────────────────────────────────────────────────

enum ToolState {
    case pending, running, done, error
}

struct ToolCallCard: View {
    let tool: String
    let args: [String: AnyCodable]
    let state: ToolState
    let durationMs: Int?
    let output: String?

    private var icon: String {
        switch state {
        case .pending: return "clock"
        case .running: return "gearshape"
        case .done: return "checkmark.circle"
        case .error: return "xmark.circle"
        }
    }

    private var color: Color {
        switch state {
        case .pending: return .secondary
        case .running: return .accentColor
        case .done: return .green
        case .error: return .red
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.caption)
                    .foregroundColor(color)
                    .if(state == .running) { view in
                        view.symbolEffect(.pulse)
                    }

                Text(tool)
                    .font(.caption.monospaced().bold())
                    .foregroundColor(color)

                if !args.isEmpty {
                    Text(formatArgs(args))
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }

                Spacer()

                if let ms = durationMs {
                    Text("\(ms)ms")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                } else if state == .running {
                    ProgressView()
                        .scaleEffect(0.4)
                        .frame(width: 12, height: 12)
                }
            }

            if let output = output, !output.isEmpty {
                Text(String(output.prefix(200)))
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
                    .padding(.leading, 20)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(color.opacity(0.08))
        .cornerRadius(6)
    }

    private func formatArgs(_ args: [String: AnyCodable]) -> String {
        let pairs = args.prefix(2).map { "\($0.key): \(String(describing: $0.value.value))" }
        let rest = args.count > 2 ? " +\(args.count - 2)" : ""
        return pairs.joined(separator: ", ") + rest
    }
}

// ─────────────────────────────────────────────────────────────────
// AgentThinkingBubble — reasoning text bubble
// ─────────────────────────────────────────────────────────────────

struct AgentThinkingBubble: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Image(systemName: "brain")
                .font(.caption2)
                .foregroundColor(.purple)

            Text(text.prefix(500))
                .font(.caption)
                .foregroundColor(.secondary)
                .italic()
                .lineLimit(5)

            if text.count > 500 {
                Text("...")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.purple.opacity(0.06))
        .cornerRadius(6)
    }
}

// ─────────────────────────────────────────────────────────────────
// IterationProgressBar — ReAct iteration progress indicator
// ─────────────────────────────────────────────────────────────────

struct IterationProgressBar: View {
    let current: Int
    let max: Int

    var body: some View {
        HStack(spacing: 4) {
            Text("Step \(current)/\(max)")
                .font(.caption2)
                .foregroundColor(.secondary)

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(Color.secondary.opacity(0.2))
                        .frame(height: 4)
                        .cornerRadius(2)

                    Rectangle()
                        .fill(Color.accentColor)
                        .frame(width: max > 0 ? geo.size.width * CGFloat(current) / CGFloat(max) : 0, height: 4)
                        .cornerRadius(2)
                }
            }
            .frame(width: 60, height: 4)
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// View Modifier helpers
// ─────────────────────────────────────────────────────────────────

extension View {
    @ViewBuilder
    func `if`<Content: View>(_ condition: Bool, transform: (Self) -> Content) -> some View {
        if condition {
            transform(self)
        } else {
            self
        }
    }
}
