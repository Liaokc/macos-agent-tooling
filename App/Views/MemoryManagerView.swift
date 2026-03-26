import SwiftUI

// ─────────────────────────────────────────────────────────────────
// MemoryManagerView — memory management panel
// ─────────────────────────────────────────────────────────────────

struct MemoryManagerView: View {
    @State private var entries: [AgentBridge.MemoryEntry] = []
    @State private var total: Int = 0
    @State private var dbSizeBytes: Int = 0
    @State private var selectedType: String? = nil
    @State private var searchText: String = ""
    @State private var selectedIds: Set<String> = []
    @State private var isLoading: Bool = false
    @State private var showDeleteAlert: Bool = false
    @State private var showClearAlert: Bool = false
    @State private var loadError: String?

    var body: some View {
        VStack(spacing: 0) {
            // ── Usage Stats ───────────────────────────────────────────────
            HStack(spacing: 24) {
                statCard("Semantic", count: semanticCount, icon: "brain")
                statCard("Episodic", count: episodicCount, icon: "clock")
                statCard("Total", count: total, icon: "memorychip")
                Spacer()
                Text(formatBytes(dbSizeBytes))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding()
            .background(Color(nsColor: .controlBackgroundColor))

            Divider()

            // ── Search ─────────────────────────────────────────────────────
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary)
                TextField("Search memories...", text: $searchText)
                    .textFieldStyle(.plain)
                    .onSubmit { Task { await searchMemories() } }
                if !searchText.isEmpty {
                    Button {
                        searchText = ""
                        Task { await loadMemories() }
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(10)
            .background(Color(nsColor: .textBackgroundColor))
            .cornerRadius(8)
            .padding(.horizontal)
            .padding(.top, 12)

            // ── Type Filter ────────────────────────────────────────────────
            Picker("Filter", selection: $selectedType) {
                Text("All").tag(nil as String?)
                Text("Semantic").tag("semantic" as String?)
                Text("Episodic").tag("episodic" as String?)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.vertical, 8)
            .onChange(of: selectedType) { _, _ in
                Task { await loadMemories() }
            }

            // ── Memory List ────────────────────────────────────────────────
            if isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if entries.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "memorychip")
                        .font(.largeTitle)
                        .foregroundColor(.secondary)
                    Text("No memories found")
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(entries, selection: $selectedIds) { entry in
                    MemoryEntryRow(entry: entry)
                        .tag(entry.id)
                }
                .listStyle(.plain)
            }

            if let err = loadError {
                Text(err)
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.horizontal)
            }

            Divider()

            // ── Actions ────────────────────────────────────────────────────
            HStack {
                Button("Delete Selected (\(selectedIds.count))") {
                    showDeleteAlert = true
                }
                .disabled(selectedIds.isEmpty)

                Spacer()

                Button("Clear All Semantic") {
                    selectedType = "semantic"
                    showClearAlert = true
                }

                Button("Clear All") {
                    selectedType = nil
                    showClearAlert = true
                }
                .foregroundColor(.red)

                Button("Refresh") {
                    Task { await loadMemories() }
                }
            }
            .padding()
        }
        .frame(minWidth: 600, minHeight: 400)
        .task {
            await loadMemories()
        }
        .alert("Delete \(selectedIds.count) memories?", isPresented: $showDeleteAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                Task { await deleteSelected() }
            }
        }
        .alert("Clear \(selectedType ?? "all") memories?", isPresented: $showClearAlert) {
            Button("Cancel", role: .cancel) {}
            Button("Clear", role: .destructive) {
                Task { await clearMemories() }
            }
        }
    }

    // ─── Computed ─────────────────────────────────────────────────────────────

    private var semanticCount: Int {
        entries.filter { $0.memoryType == "semantic" }.count
    }

    private var episodicCount: Int {
        entries.filter { $0.memoryType == "episodic" }.count
    }

    // ─── Data Operations ─────────────────────────────────────────────────────

    private func loadMemories() async {
        isLoading = true
        loadError = nil
        do {
            let resp = try await AgentBridge.shared.memoryList(
                type: selectedType, limit: 100
            )
            entries = resp.entries
            total = resp.total
            dbSizeBytes = resp.dbSizeBytes
        } catch {
            loadError = error.localizedDescription
        }
        isLoading = false
    }

    private func searchMemories() async {
        guard !searchText.isEmpty else {
            await loadMemories()
            return
        }
        isLoading = true
        do {
            let results = try await AgentBridge.shared.memorySearch(
                query: searchText, topK: 20
            )
            entries = results.map { $0.entry }
            total = entries.count
        } catch {
            await loadMemories()
        }
        isLoading = false
    }

    private func deleteSelected() async {
        for id in selectedIds {
            _ = try? await AgentBridge.shared.memoryDelete(id: id)
        }
        selectedIds.removeAll()
        await loadMemories()
    }

    private func clearMemories() async {
        _ = try? await AgentBridge.shared.memoryClear(type: selectedType)
        selectedIds.removeAll()
        await loadMemories()
    }

    // ─── Helpers ─────────────────────────────────────────────────────────────

    private func statCard(_ label: String, count: Int, icon: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundColor(.secondary)
            VStack(alignment: .leading, spacing: 0) {
                Text("\(count)")
                    .font(.headline)
                Text(label)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func formatBytes(_ bytes: Int) -> String {
        let kb = Double(bytes) / 1024.0
        if kb < 1024 {
            return String(format: "%.1f KB", kb)
        }
        let mb = kb / 1024.0
        return String(format: "%.1f MB", mb)
    }
}

// ─────────────────────────────────────────────────────────────────
// MemoryEntryRow — single memory row
// ─────────────────────────────────────────────────────────────────

struct MemoryEntryRow: View {
    let entry: AgentBridge.MemoryEntry

    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Primary row
            HStack {
                Circle()
                    .fill(entry.memoryType == "semantic" ? Color.purple : Color.orange)
                    .frame(width: 8, height: 8)

                Text(entry.content.prefix(80) + (entry.content.count > 80 ? "..." : ""))
                    .font(.body)
                    .lineLimit(isExpanded ? nil : 2)
                    .onTapGesture {
                        withAnimation { isExpanded.toggle() }
                    }

                Spacer()

                Text(entry.memoryType)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(
                        (entry.memoryType == "semantic" ? Color.purple : Color.orange)
                            .opacity(0.1)
                    )
                    .cornerRadius(4)
            }

            // Expanded details
            if isExpanded {
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.content)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.leading, 12)

                    HStack {
                        Text(formatDate(entry.createdAt))
                            .font(.caption2)
                            .foregroundColor(.secondary)

                        Spacer()

                        HStack(spacing: 1) {
                            ForEach(0..<min(Int(entry.importance * 5), 5), id: \.self) { _ in
                                Image(systemName: "star.fill")
                                    .font(.caption2)
                                    .foregroundColor(.orange)
                            }
                        }
                    }
                    .padding(.leading, 12)
                }
            } else {
                HStack {
                    Text(formatDate(entry.createdAt))
                        .font(.caption2)
                        .foregroundColor(.secondary)

                    Spacer()
                }
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private func formatDate(_ ts: Double) -> String {
        let d = Date(timeIntervalSince1970: ts)
        let f = DateFormatter()
        f.dateStyle = .short
        f.timeStyle = .short
        return f.string(from: d)
    }
}
