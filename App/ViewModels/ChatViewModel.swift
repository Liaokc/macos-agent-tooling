import Foundation
import Combine

@MainActor
final class ChatViewModel: ObservableObject {
    // ─────────────────────────────────────────────────────────────
    // Published State
    // ─────────────────────────────────────────────────────────────

    @Published var messages: [MessageItem] = []
    @Published var inputText: String = ""
    @Published var isLoading: Bool = false
    @Published var isConnected: Bool = false
    @Published var availableModels: [ModelInfo] = []
    @Published var selectedModel: String = "llama3"
    @Published var sessions: [SessionSummary] = []
    @Published var currentSession: SessionSummary?
    @Published var errorMessage: String?
    @Published var hardwareStats: HardwareStats?

    // ─────────────────────────────────────────────────────────────
    // Private
    // ─────────────────────────────────────────────────────────────

    private let bridge = AgentBridge.shared
    private var statsTimer: Task<Void, Never>?

    init() {
        Task { await initialize() }
    }

    // ─────────────────────────────────────────────────────────────
    // Initialization
    // ─────────────────────────────────────────────────────────────

    func initialize() async {
        do {
            isConnected = try await bridge.ping()
            await loadModels()
            await loadSessions()
            await createNewSessionIfNeeded()
            startStatsPolling()
        } catch {
            errorMessage = "Failed to connect: \(error.localizedDescription)"
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Model Management
    // ─────────────────────────────────────────────────────────────

    func loadModels() async {
        do {
            availableModels = try await bridge.listModels()
            if selectedModel.isEmpty, let first = availableModels.first {
                selectedModel = first.name
            }
        } catch {
            errorMessage = "Failed to load models: \(error.localizedDescription)"
        }
    }

    func pullModel(_ model: String) async {
        do {
            try await bridge.pullModel(model)
            await loadModels()
        } catch {
            errorMessage = "Failed to pull model: \(error.localizedDescription)"
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Session Management
    // ─────────────────────────────────────────────────────────────

    func loadSessions() async {
        do {
            sessions = try await bridge.listSessions()
        } catch {
            // Non-critical
        }
    }

    func createNewSessionIfNeeded() async {
        if currentSession == nil {
            await createSession(title: "New Chat")
        }
    }

    func createSession(title: String = "New Chat") async {
        do {
            let session = try await bridge.createSession(model: selectedModel, title: title)
            currentSession = SessionSummary(
                id: session.id,
                title: session.title,
                model: session.model,
                createdAt: session.createdAt,
                updatedAt: session.updatedAt,
                messageCount: 0
            )
            messages = []
            await loadSessions()
        } catch {
            errorMessage = "Failed to create session: \(error.localizedDescription)"
        }
    }

    func switchSession(_ session: SessionSummary) async {
        guard currentSession?.id != session.id else { return }
        currentSession = session
        await loadMessages(for: session.id)
    }

    func loadMessages(for sessionId: String) async {
        do {
            let dbMessages = try await bridge.getMessages(sessionId: sessionId)
            messages = dbMessages.map { dbMsg in
                MessageItem(
                    id: dbMsg.id,
                    role: dbMsg.role == "assistant" ? .assistant : .user,
                    content: dbMsg.content
                )
            }
        } catch {
            errorMessage = "Failed to load messages: \(error.localizedDescription)"
        }
    }

    func deleteSession(_ session: SessionSummary) async {
        do {
            try await bridge.deleteSession(session.id)
            if currentSession?.id == session.id {
                messages = []
                currentSession = nil
                await createNewSessionIfNeeded()
            }
            await loadSessions()
        } catch {
            errorMessage = "Failed to delete session: \(error.localizedDescription)"
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Send Message
    // ─────────────────────────────────────────────────────────────

    func sendMessage() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let session = currentSession else { return }

        inputText = ""
        isLoading = true
        errorMessage = nil

        // Add user message optimistically
        let userMsg = MessageItem(id: UUID().uuidString, role: .user, content: text)
        messages.append(userMsg)

        // Persist user message
        do {
            _ = try await bridge.addMessage(sessionId: session.id, role: "user", content: text)
        } catch {
            // Non-critical
        }

        // Build message history for API
        let apiMessages = messages.map { $0.toAPIMessage }

        // Stream response
        let responseId = UUID().uuidString
        var assistantContent = ""

        do {
            let stream = bridge.chatStream(messages: apiMessages, model: selectedModel)
            for try await token in stream {
                if let lastIdx = messages.lastIndex(where: { $0.id == responseId }) {
                    messages[lastIdx].content += token
                } else {
                    messages.append(MessageItem(id: responseId, role: .assistant, content: token))
                }
                assistantContent += token
            }

            // Persist assistant message
            if !assistantContent.isEmpty {
                _ = try await bridge.addMessage(sessionId: session.id, role: "assistant", content: assistantContent)
            }
        } catch {
            errorMessage = "Chat error: \(error.localizedDescription)"
            // Remove the failed message
            if let lastIdx = messages.lastIndex(where: { $0.id == responseId }) {
                messages.remove(at: lastIdx)
            }
        }

        isLoading = false
        await loadSessions() // Refresh to update updatedAt
    }

    // ─────────────────────────────────────────────────────────────
    // Hardware Stats
    // ─────────────────────────────────────────────────────────────

    func startStatsPolling() {
        statsTimer?.cancel()
        statsTimer = Task {
            while !Task.isCancelled {
                do {
                    hardwareStats = try await bridge.getStats()
                } catch {
                    // Non-critical
                }
                try? await Task.sleep(nanoseconds: 5_000_000_000) // 5s
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// Message Item
// ─────────────────────────────────────────────────────────────────

struct MessageItem: Identifiable, Equatable {
    let id: String
    let role: Role
    var content: String
    var isStreaming: Bool = false

    enum Role: String {
        case user
        case assistant
    }

    var toAPIMessage: [String: String] {
        ["role": role == .user ? "user" : "assistant", "content": content]
    }

    static func == (lhs: MessageItem, rhs: MessageItem) -> Bool {
        lhs.id == rhs.id
    }
}
