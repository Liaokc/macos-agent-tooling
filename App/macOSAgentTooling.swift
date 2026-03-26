import AppKit
import SwiftUI

@main
struct MainApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowStyle(.automatic)
        .defaultSize(width: 900, height: 700)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}
