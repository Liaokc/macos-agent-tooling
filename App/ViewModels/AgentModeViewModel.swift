import Foundation
import Combine

// ─────────────────────────────────────────────────────────────────
// AgentModeViewModel — manages agent activity stream state
// ─────────────────────────────────────────────────────────────────

@MainActor
final class AgentModeViewModel: ObservableObject {
    @Published var isActive: Bool = false
    @Published var activities: [AgentActivityItem] = []
    @Published var currentIteration: Int = 0
    @Published var maxIterations: Int = 10
    @Published var totalDuration: TimeInterval = 0

    private var streamTask: Task<Void, Never>?

    func startStream(task: String, sessionId: String, model: String = "llama3", maxIterations: Int = 10) {
        stopStream()
        self.isActive = true
        self.maxIterations = maxIterations
        self.activities = []
        self.currentIteration = 0
        self.totalDuration = 0

        streamTask = Task {
            let bridge = AgentBridge.shared
            do {
                for try await event in bridge.agentStream(task: task, sessionId: sessionId, model: model) {
                    await MainActor.run {
                        self.handleEvent(event)
                    }
                }
            } catch {
                await MainActor.run {
                    self.activities.append(.error(id: UUID(), message: error.localizedDescription))
                    self.isActive = false
                }
            }
        }
    }

    func stopStream() {
        streamTask?.cancel()
        streamTask = nil
        isActive = false
    }

    private func handleEvent(_ event: AgentBridge.AgentStreamEvent) {
        switch event {
        case .thinking(let text):
            activities.append(.thinking(id: UUID(), text: text))
        case .iterationStart(let number):
            currentIteration = number
            activities.append(.iterationStart(id: UUID(), number: number))
        case .toolCall(let tool, let args, let callId):
            activities.append(.toolCall(id: UUID(), tool: tool, args: args, callId: callId))
        case .toolResult(let tool, let output, let success, let durationMs):
            // Find matching tool call and update
            if let idx = activities.lastIndex(where: {
                if case .toolCall(_, let t, _, _) = $0 { return t == tool } else { return false }
            }) {
                activities[idx] = .toolResult(id: UUID(), tool: tool, output: output, success: success, durationMs: durationMs)
            } else {
                activities.append(.toolResult(id: UUID(), tool: tool, output: output, success: success, durationMs: durationMs))
            }
        case .textChunk(let text):
            activities.append(.textChunk(id: UUID(), text: text))
        case .done(let response):
            activities.append(.done(id: UUID(), finalText: response))
            isActive = false
        case .error(let message):
            activities.append(.error(id: UUID(), message: message))
            isActive = false
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// AgentActivityItem — activity log entries for Agent Mode
// ─────────────────────────────────────────────────────────────────

enum AgentActivityItem: Identifiable {
    case thinking(id: UUID, text: String)
    case iterationStart(id: UUID, number: Int)
    case toolCall(id: UUID, tool: String, args: [String: AnyCodable], callId: String)
    case toolResult(id: UUID, tool: String, output: String, success: Bool, durationMs: Int)
    case textChunk(id: UUID, text: String)
    case done(id: UUID, finalText: String)
    case error(id: UUID, message: String)

    var id: UUID {
        switch self {
        case .thinking(let id, _), .iterationStart(let id, _),
             .toolCall(let id, _, _, _), .toolResult(let id, _, _, _, _),
             .textChunk(let id, _), .done(let id, _), .error(let id, _):
            return id
        }
    }
}
