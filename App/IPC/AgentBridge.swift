import Foundation

// ─────────────────────────────────────────────────────────────────
// Shared Models (mirror of Python shared_types.py)
// ─────────────────────────────────────────────────────────────────

struct ModelInfo: Identifiable, Codable, Sendable {
    var id: String { name }
    let name: String
    let size: Int
    let modifiedAt: Double
    let digest: String

    enum CodingKeys: String, CodingKey {
        case name, size, digest
        case modifiedAt = "modified_at"
    }
}

struct HardwareStats: Codable, Sendable {
    let cpuPercent: Double
    let memoryUsed: Int
    let memoryTotal: Int
    let memoryPercent: Double
    let gpuStats: [[String: AnyCodable]]

    enum CodingKeys: String, CodingKey {
        case cpuPercent = "cpu_percent"
        case memoryUsed = "memory_used"
        case memoryTotal = "memory_total"
        case memoryPercent = "memory_percent"
        case gpuStats = "gpu_stats"
    }
}

struct ChatMessage: Identifiable, Codable, Sendable {
    let id: String
    let sessionId: String
    let role: String
    let content: String
    let createdAt: Int

    enum CodingKeys: String, CodingKey {
        case id, role, content
        case sessionId = "session_id"
        case createdAt = "created_at"
    }
}

struct SessionInfo: Identifiable, Codable, Sendable {
    var id: String { _id }
    let _id: String
    let title: String
    let model: String
    let createdAt: Int
    let updatedAt: Int
    let deletedAt: Int?
    let messageCount: Int?

    enum CodingKeys: String, CodingKey {
        case _id = "id", title, model
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case deletedAt = "deleted_at"
        case messageCount = "message_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Handle both "id" and "_id" keys
        if let id = try? c.decode(String.self, forKey: ._id) {
            self._id = id
        } else {
            self._id = try c.decode(String.self, forKey: ._id)
        }
        self.title = try c.decode(String.self, forKey: .title)
        self.model = try c.decode(String.self, forKey: .model)
        self.createdAt = try c.decode(Int.self, forKey: .createdAt)
        self.updatedAt = try c.decode(Int.self, forKey: .updatedAt)
        self.deletedAt = try c.decodeIfPresent(Int.self, forKey: .deletedAt)
        self.messageCount = try c.decodeIfPresent(Int.self, forKey: .messageCount)
    }
}

struct SessionSummary: Identifiable, Codable, Sendable {
    let id: String
    let title: String
    let model: String
    let createdAt: Int
    let updatedAt: Int
    let messageCount: Int

    enum CodingKeys: String, CodingKey {
        case id, title, model
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case messageCount = "message_count"
    }
}

// ─────────────────────────────────────────────────────────────────
// AnyCodable helper
// ─────────────────────────────────────────────────────────────────

struct AnyCodable: Codable, Sendable {
    let value: Any

    init(_ value: Any) { self.value = value }
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = i }
        else if let d = try? c.decode(Double.self) { value = d }
        else if let s = try? c.decode(String.self) { value = s }
        else if let b = try? c.decode(Bool.self) { value = b }
        else if let arr = try? c.decode([AnyCodable].self) { value = arr.map(\.value) }
        else if let dict = try? c.decode([String: AnyCodable].self) { value = dict.mapValues(\.value) }
        else { value = NSNull() }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        if let i = value as? Int { try c.encode(i) }
        else if let d = value as? Double { try c.encode(d) }
        else if let s = value as? String { try c.encode(s) }
        else if let b = value as? Bool { try c.encode(b) }
        else { try c.encodeNil() }
    }
}

// ─────────────────────────────────────────────────────────────────
// IPC Errors
// ─────────────────────────────────────────────────────────────────

enum AgentBridgeError: Error, LocalizedError {
    case processNotRunning
    case invalidResponse
    case serverError(String)
    case timeout

    var errorDescription: String? {
        switch self {
        case .processNotRunning: return "Python bridge process is not running"
        case .invalidResponse: return "Invalid response from bridge"
        case .serverError(let msg): return "Bridge error: \(msg)"
        case .timeout: return "Bridge request timed out"
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// AgentBridge — Swift ↔ Python IPC
// ─────────────────────────────────────────────────────────────────

actor AgentBridge {
    static let shared = AgentBridge()

    private var process: Process?
    private var stdin: Pipe?
    private var stdout: Pipe?
    private var pendingRequests: [String: CheckedContinuation<Data, Error>] = [:]
    private var readBuffer = Data()
    private var isRunning = false
    private let queue = DispatchQueue(label: "com.cheng-agent.bridge", qos: .userInitiated)

    private let corePath: String

    private init() {
        // Locate the Python core executable
        let envPath = FileManager.default.environment["AGENT_TOOLING_CORE_PATH"]
        self.corePath = envPath ?? Bundle.main.resourcePath.map { "\($0)/../Frameworks/Core/ipc.py" } ?? "ipc.py"
    }

    // ─────────────────────────────────────────────────────────────
    // Lifecycle
    // ─────────────────────────────────────────────────────────────

    func start() throws {
        guard !isRunning else { return }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-c",
            """
            import sys, os
            sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/macos-agent-tooling/Core'))
            from ipc import run_server; run_server()
            """
        ]

        let sout = Pipe()
        let sin = Pipe()
        p.standardOutput = sout
        p.standardInput = sin

        stdout = sout
        stdin = sin
        process = p

        p.terminationHandler = { [weak self] _ in
            Task { await self?.handleTermination() }
        }

        // Read stdout asynchronously
        sout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if !data.isEmpty {
                Task { await self?.handleData(data) }
            }
        }

        p.start()
        isRunning = true
    }

    func stop() {
        guard isRunning else { return }
        process?.terminate()
        stdout?.fileHandleForReading.readabilityHandler = nil
        stdin = nil
        stdout = nil
        process = nil
        isRunning = false
    }

    private func handleTermination() {
        isRunning = false
        // Cancel all pending requests
        let conts = pendingRequests.values
        pendingRequests.removeAll()
        for c in conts {
            c.resume(returning: Data())
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Data handling
    // ─────────────────────────────────────────────────────────────

    private func handleData(_ data: Data) {
        readBuffer.append(data)

        // Process complete lines
        while let newlineIndex = readBuffer.firstIndex(of: UInt8(ascii: "\n")) {
            let lineData = readBuffer[..<newlineIndex]
            readBuffer = readBuffer[(readBuffer.index(after: newlineIndex))...]

            guard !lineData.isEmpty else { continue }

            // Extract request_id from line to match continuation
            if let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
               let reqId = json["request_id"] as? String,
               let cont = pendingRequests.removeValue(forKey: reqId) {
                cont.resume(returning: lineData)
            } else if let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                      let reqId = json["request_id"] as? String {
                // Matched but no pending continuation
                pendingRequests.removeValue(forKey: reqId)
            }
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Request/Response
    // ─────────────────────────────────────────────────────────────

    private func sendRequest(cmd: String, args: [String: Any] = [:]) async throws -> [String: Any] {
        if !isRunning {
            try start()
        }

        let requestId = UUID().uuidString
        let request: [String: Any] = [
            "cmd": cmd,
            "args": args,
            "request_id": requestId
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: request),
              let jsonStr = String(data: jsonData, encoding: .utf8) else {
            throw AgentBridgeError.invalidResponse
        }

        return try await withCheckedThrowingContinuation { cont in
            pendingRequests[requestId] = cont
            stdin?.fileHandleForWriting.write("\(jsonStr)\n")
        }.flatMap { data -> [String: Any] in
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw AgentBridgeError.invalidResponse
            }
            return json
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Public API
    // ─────────────────────────────────────────────────────────────

    func ping() async throws -> Bool {
        let resp = try await sendRequest(cmd: "ping")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        return (resp["data"] as? [String: Any])?["connected"] as? Bool ?? false
    }

    func listModels() async throws -> [ModelInfo] {
        let resp = try await sendRequest(cmd: "list_models")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        let data = resp["data"] as? [[String: Any]] ?? []
        return data.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
            .compactMap { try? JSONDecoder().decode(ModelInfo.self, from: $0) }
    }

    func pullModel(_ model: String) async throws {
        let resp = try await sendRequest(cmd: "pull_model", args: ["model": model])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
    }

    func getStats() async throws -> HardwareStats {
        let resp = try await sendRequest(cmd: "get_stats")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data),
              let stats = try? JSONDecoder().decode(HardwareStats.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return stats
    }

    // ─────────────────────────────────────────────────────────────
    // Session Management
    // ─────────────────────────────────────────────────────────────

    func createSession(model: String, title: String = "New Chat") async throws -> SessionInfo {
        let resp = try await sendRequest(cmd: "create_session", args: ["model": model, "title": title])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data),
              let session = try? JSONDecoder().decode(SessionInfo.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return session
    }

    func listSessions() async throws -> [SessionSummary] {
        let resp = try await sendRequest(cmd: "list_sessions")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        let data = resp["data"] as? [[String: Any]] ?? []
        return data.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
            .compactMap { try? JSONDecoder().decode(SessionSummary.self, from: $0) }
    }

    func getSession(_ sessionId: String) async throws -> SessionInfo? {
        let resp = try await sendRequest(cmd: "get_session", args: ["session_id": sessionId])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data ?? [:]),
              let session = try? JSONDecoder().decode(SessionInfo.self, from: jsonData) else {
            return nil
        }
        return session
    }

    func getMessages(sessionId: String) async throws -> [ChatMessage] {
        let resp = try await sendRequest(cmd: "get_messages", args: ["session_id": sessionId])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        let data = resp["data"] as? [[String: Any]] ?? []
        return data.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
            .compactMap { try? JSONDecoder().decode(ChatMessage.self, from: $0) }
    }

    func addMessage(sessionId: String, role: String, content: String) async throws -> ChatMessage {
        let resp = try await sendRequest(cmd: "add_message", args: [
            "session_id": sessionId,
            "role": role,
            "content": content
        ])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data),
              let msg = try? JSONDecoder().decode(ChatMessage.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return msg
    }

    func deleteSession(_ sessionId: String) async throws {
        let resp = try await sendRequest(cmd: "delete_session", args: ["session_id": sessionId])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
    }

    func updateSessionTitle(sessionId: String, title: String) async throws {
        let resp = try await sendRequest(cmd: "update_session", args: [
            "session_id": sessionId,
            "title": title
        ])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 2: Tool Executor
    // ─────────────────────────────────────────────────────────────

    struct ToolSchema: Codable, Sendable {
        let name: String
        let description: String
        let inputSchema: ToolInputSchema

        enum CodingKeys: String, CodingKey {
            case name, description
            case inputSchema = "input_schema"
        }
    }

    struct ToolInputSchema: Codable, Sendable {
        let type: String
        let properties: [String: ToolProperty]
        let required: [String]?
    }

    struct ToolProperty: Codable, Sendable {
        let type: String
        let description: String?
        let `default`: AnyCodable?
    }

    func getTools() async throws -> [ToolSchema] {
        let resp = try await sendRequest(cmd: "get_tools")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        let data = resp["data"] as? [String: Any]
        let tools = data?["tools"] as? [[String: Any]] ?? []
        return tools.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
            .compactMap { try? JSONDecoder().decode(ToolSchema.self, from: $0) }
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 2: Memory Manager
    // ─────────────────────────────────────────────────────────────

    struct MemoryEntry: Codable, Sendable {
        let id: String
        let content: String
        let memoryType: String
        let sessionId: String?
        let importance: Float
        let createdAt: Double
        let metadata: [String: AnyCodable]

        enum CodingKeys: String, CodingKey {
            case id, content, importance, metadata
            case memoryType = "memory_type"
            case sessionId = "session_id"
            case createdAt = "created_at"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            content = try c.decode(String.self, forKey: .content)
            memoryType = try c.decode(String.self, forKey: .memoryType)
            sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId)
            importance = try c.decode(Float.self, forKey: .importance)
            createdAt = try c.decode(Double.self, forKey: .createdAt)
            metadata = (try? c.decode([String: AnyCodable].self, forKey: .metadata)) ?? [:]
        }
    }

    struct MemorySearchResult: Codable, Sendable {
        let entry: MemoryEntry
        let score: Float
    }

    func memorySearch(query: String, topK: Int = 5, types: [String]? = nil) async throws -> [MemorySearchResult] {
        var args: [String: Any] = ["query": query, "top_k": topK]
        if let types = types {
            args["types"] = types
        }
        let resp = try await sendRequest(cmd: "memory_search", args: args)
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        let data = resp["data"] as? [String: Any]
        let results = data?["results"] as? [[String: Any]] ?? []
        return results.compactMap { try? JSONSerialization.data(withJSONObject: $0) }
            .compactMap { try? JSONDecoder().decode(MemorySearchResult.self, from: $0) }
    }

    func memoryAdd(content: String, type: String = "semantic", importance: Float = 0.5, sessionId: String? = nil) async throws -> String {
        var args: [String: Any] = [
            "content": content,
            "type": type,
            "importance": importance,
        ]
        if let sid = sessionId {
            args["session_id"] = sid
        }
        let resp = try await sendRequest(cmd: "memory_add", args: args)
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        return (resp["data"] as? [String: Any])?["id"] as? String ?? ""
    }

    struct MemoryCounts: Codable, Sendable {
        let episodic: Int
        let semantic: Int
    }

    func memoryCounts() async throws -> MemoryCounts {
        let resp = try await sendRequest(cmd: "memory_counts")
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data),
              let counts = try? JSONDecoder().decode(MemoryCounts.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return counts
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 3: Config
    // ─────────────────────────────────────────────────────────────

    struct AgentConfig: Codable, Sendable {
        var model: String
        var maxIterations: Int
        var temperature: Float
        var memorySemanticEnabled: Bool
        var memoryEpisodicEnabled: Bool
        var memoryPruneDays: Int
        var systemPrompt: String?
        var showThinking: Bool
        var toolConfirmation: Bool
        var sandboxWorkspace: String

        enum CodingKeys: String, CodingKey {
            case model
            case maxIterations = "max_iterations"
            case temperature
            case memorySemanticEnabled = "memory_semantic_enabled"
            case memoryEpisodicEnabled = "memory_episodic_enabled"
            case memoryPruneDays = "memory_prune_days"
            case systemPrompt = "system_prompt"
            case showThinking = "show_thinking"
            case toolConfirmation = "tool_confirmation"
            case sandboxWorkspace = "sandbox_workspace"
        }

        init(model: String = "llama3", maxIterations: Int = 10, temperature: Float = 0.7,
             memorySemanticEnabled: Bool = true, memoryEpisodicEnabled: Bool = true,
             memoryPruneDays: Int = 30, systemPrompt: String? = nil,
             showThinking: Bool = true, toolConfirmation: Bool = true,
             sandboxWorkspace: String = "~/.macos-agent-workspace") {
            self.model = model
            self.maxIterations = maxIterations
            self.temperature = temperature
            self.memorySemanticEnabled = memorySemanticEnabled
            self.memoryEpisodicEnabled = memoryEpisodicEnabled
            self.memoryPruneDays = memoryPruneDays
            self.systemPrompt = systemPrompt
            self.showThinking = showThinking
            self.toolConfirmation = toolConfirmation
            self.sandboxWorkspace = sandboxWorkspace
        }
    }

    func getConfig() async throws -> AgentConfig {
        let resp = try await sendRequest(cmd: "get_config", args: [:])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let configDict = data["config"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: configDict),
              let cfg = try? JSONDecoder().decode(AgentConfig.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return cfg
    }

    func updateConfig(_ config: AgentConfig) async throws -> AgentConfig {
        let configDict = try config.toDictionary()
        let resp = try await sendRequest(cmd: "update_config", args: configDict)
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let configDict = data["config"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: configDict),
              let cfg = try? JSONDecoder().decode(AgentConfig.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return cfg
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 3: Memory list/delete/clear
    // ─────────────────────────────────────────────────────────────

    struct MemoryListResponse: Codable, Sendable {
        let entries: [MemoryEntry]
        let total: Int
        let dbSizeBytes: Int

        enum CodingKeys: String, CodingKey {
            case entries, total
            case dbSizeBytes = "db_size_bytes"
        }
    }

    func memoryList(type: String? = nil, limit: Int = 50, offset: Int = 0) async throws -> MemoryListResponse {
        var args: [String: Any] = ["limit": limit, "offset": offset]
        if let t = type { args["type"] = t }
        let resp = try await sendRequest(cmd: "memory_list", args: args)
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        guard let data = resp["data"] as? [String: Any],
              let jsonData = try? JSONSerialization.data(withJSONObject: data),
              let result = try? JSONDecoder().decode(MemoryListResponse.self, from: jsonData) else {
            throw AgentBridgeError.invalidResponse
        }
        return result
    }

    func memoryDelete(id: String) async throws -> Bool {
        let resp = try await sendRequest(cmd: "memory_delete", args: ["id": id])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        return (resp["data"] as? [String: Any])?["deleted"] as? Bool ?? false
    }

    func memoryClear(type: String? = nil) async throws -> Int {
        var args: [String: Any] = [:]
        if let t = type { args["type"] = t }
        let resp = try await sendRequest(cmd: "memory_clear", args: args)
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        return (resp["data"] as? [String: Any])?["cleared"] as? Int ?? 0
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 3: Extended AgentStreamEvent
    // ─────────────────────────────────────────────────────────────

    enum AgentStreamEvent: Sendable {
        case thinking(text: String)
        case iterationStart(number: Int)
        case toolCall(tool: String, args: [String: AnyCodable], callId: String)
        case toolResult(tool: String, output: String, success: Bool, durationMs: Int)
        case textChunk(text: String)
        case done(response: String)
        case error(message: String)
    }

    /// Extended agentStream that supports Phase 3 THINKING + ITERATION events.
    func agentStream(task: String, sessionId: String, model: String = "llama3") -> AsyncThrowingStream<AgentStreamEvent, Error> {
        AsyncThrowingStream { cont in
            Task {
                do {
                    let p = Process()
                    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                    p.arguments = ["-c",
                        """
                        import sys, os, asyncio, json
                        sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/macos-agent-tooling/Core'))
                        from ipc import run_server
                        asyncio.run(run_server())
                        """
                    ]

                    let sin = Pipe()
                    let sout = Pipe()
                    p.standardInput = sin
                    p.standardOutput = sout

                    p.start()

                    let request: [String: Any] = [
                        "cmd": "_agent_stream",
                        "args": [
                            "task": task,
                            "session_id": sessionId,
                            "model": model,
                        ],
                        "request_id": UUID().uuidString,
                    ]
                    let jsonData = try JSONSerialization.data(withJSONObject: request)
                    sin.fileHandleForWriting.write(jsonData)
                    sin.fileHandleForWriting.write(Data("\n".utf8))
                    sin.fileHandleForWriting.closeFile()

                    let handle = sout.fileHandleForReading
                    while true {
                        let data = handle.availableData
                        if data.isEmpty { break }
                        guard let line = String(data: data, encoding: .utf8) else { continue }
                        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty,
                              let lineData = trimmed.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                              let eventStr = json["event"] as? String,
                              let eventData = json["data"] as? [String: Any] else {
                            continue
                        }

                        let streamEvent: AgentStreamEvent?
                        switch eventStr {
                        case "thinking":
                            let text = eventData["text"] as? String ?? ""
                            streamEvent = .thinking(text: text)
                        case "iteration":
                            let number = eventData["number"] as? Int ?? 0
                            streamEvent = .iterationStart(number: number)
                        case "tool_call":
                            let tool = eventData["tool"] as? String ?? ""
                            let args = (eventData["args"] as? [String: Any]) ?? [:]
                            let callId = eventData["call_id"] as? String ?? UUID().uuidString
                            streamEvent = .toolCall(tool: tool, args: args.mapValues { AnyCodable($0) }, callId: callId)
                        case "tool_result":
                            let tool = eventData["tool"] as? String ?? ""
                            let output = eventData["output"] as? String ?? ""
                            let success = eventData["success"] as? Bool ?? false
                            let durationMs = eventData["duration_ms"] as? Int ?? 0
                            streamEvent = .toolResult(tool: tool, output: output, success: success, durationMs: durationMs)
                        case "text":
                            let token = eventData["token"] as? String ?? ""
                            streamEvent = .textChunk(text: token)
                        case "done":
                            let response = eventData["response"] as? String ?? ""
                            streamEvent = .done(response: response)
                        case "error":
                            let message = eventData["message"] as? String ?? ""
                            streamEvent = .error(message: message)
                        default:
                            streamEvent = nil
                        }

                        if let ev = streamEvent {
                            cont.yield(ev)
                        }

                        if eventStr == "done" || eventStr == "error" {
                            break
                        }
                    }

                    cont.finish()
                    p.waitUntilExit()
                } catch {
                    cont.finish(throwing: error)
                }
            }
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Phase 2: Agent Executor
    // ─────────────────────────────────────────────────────────────

    struct AgentEvent: Codable, Sendable {
        let type: String   // "tool_call" | "tool_result" | "text" | "done" | "error"
        let data: [String: AnyCodable]

        enum CodingKeys: String, CodingKey {
            case type, data
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            type = try c.decode(String.self, forKey: .type)
            data = (try? c.decode([String: AnyCodable].self, forKey: .data)) ?? [:]
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(type, forKey: .type)
            try c.encode(data, forKey: .data)
        }
    }

    func agentExecute(task: String, sessionId: String, model: String = "llama3") async throws -> String {
        let resp = try await sendRequest(cmd: "agent_execute", args: [
            "task": task,
            "session_id": sessionId,
            "model": model,
        ])
        guard resp["ok"] as? Bool == true else {
            throw AgentBridgeError.serverError(resp["error"] as? String ?? "unknown")
        }
        return (resp["data"] as? [String: Any])?["result"] as? String ?? ""
    }

    /// Stream agent execution events.
    /// Each yielded element is a JSON-encoded AgentEvent string.
    func agentStream(task: String, sessionId: String, model: String = "llama3") -> AsyncThrowingStream<AgentEvent, Error> {
        AsyncThrowingStream { cont in
            Task {
                do {
                    let p = Process()
                    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                    p.arguments = ["-c",
                        """
                        import sys, os, asyncio, json
                        sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/macos-agent-tooling/Core'))
                        from ipc import run_server
                        asyncio.run(run_server())
                        """
                    ]

                    let sin = Pipe()
                    let sout = Pipe()
                    p.standardInput = sin
                    p.standardOutput = sout

                    p.start()

                    let request: [String: Any] = [
                        "cmd": "_agent_stream",
                        "args": [
                            "task": task,
                            "session_id": sessionId,
                            "model": model,
                        ],
                        "request_id": UUID().uuidString,
                    ]
                    let jsonData = try JSONSerialization.data(withJSONObject: request)
                    sin.fileHandleForWriting.write(jsonData)
                    sin.fileHandleForWriting.write(Data("\n".utf8))
                    sin.fileHandleForWriting.closeFile()

                    let handle = sout.fileHandleForReading
                    while true {
                        let data = handle.availableData
                        if data.isEmpty { break }
                        guard let line = String(data: data, encoding: .utf8) else { continue }
                        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty,
                              let lineData = trimmed.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                              let eventData = try? JSONSerialization.data(withJSONObject: json),
                              let event = try? JSONDecoder().decode(AgentEvent.self, from: eventData) else {
                            continue
                        }

                        if event.type == "error" {
                            let msg = (event.data["message"] as? AnyCodable)?.value as? String ?? "unknown"
                            throw AgentBridgeError.serverError(msg)
                        }

                        cont.yield(event)

                        if event.type == "done" {
                            break
                        }
                    }

                    cont.finish()
                    p.waitUntilExit()
                } catch {
                    cont.finish(throwing: error)
                }
            }
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Chat (streaming via helper process)
    // ─────────────────────────────────────────────────────────────

    func chatStream(messages: [[String: String]], model: String) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { cont in
            Task {
                do {
                    // Spawn a dedicated subprocess for streaming
                    let p = Process()
                    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                    p.arguments = ["-c",
                        """
                        import sys, os, asyncio, json
                        sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/macos-agent-tooling/Core'))
                        from ipc import run_server
                        asyncio.run(run_server())
                        """
                    ]

                    let sin = Pipe()
                    let sout = Pipe()
                    p.standardInput = sin
                    p.standardOutput = sout

                    p.start()

                    // Send streaming request — use _stream cmd for true streaming
                    let request: [String: Any] = [
                        "cmd": "_stream",
                        "args": ["messages": messages, "model": model],
                        "request_id": UUID().uuidString
                    ]
                    let jsonData = try JSONSerialization.data(withJSONObject: request)
                    sin.fileHandleForWriting.write(jsonData)
                    sin.fileHandleForWriting.write(Data("\n".utf8))
                    sin.fileHandleForWriting.closeFile()

                    // Read response line by line until EOF (Python closes stdout when done)
                    let handle = sout.fileHandleForReading
                    while true {
                        let data = handle.availableData
                        if data.isEmpty { break }  // EOF reached
                        guard let line = String(data: data, encoding: .utf8) else { continue }
                        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty,
                              let lineData = trimmed.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any] else {
                            continue
                        }
                        if let token = json["token"] as? String {
                            cont.yield(token)
                        } else if let err = json["error"] as? String {
                            throw AgentBridgeError.serverError(err)
                        } else if json["done"] as? Bool == true {
                            break  // Stream complete
                        }
                    }

                    cont.finish()
                    p.waitUntilExit()  // Wait for process to fully exit (P0-2 zombie fix)
                } catch {
                    cont.finish(throwing: error)
                }
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// Encodable → Dictionary helper
// ─────────────────────────────────────────────────────────────────

extension Encodable {
    func toDictionary() throws -> [String: Any] {
        let data = try JSONEncoder().encode(self)
        return (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}
