import AppKit
import SwiftUI

/// Observes the SwiftUI-owned window without replacing its delegate.
@MainActor
final class WindowPresentation: ObservableObject {
    @Published private(set) var isFullScreen = false
    private weak var window: NSWindow?
    private var observers: [NSObjectProtocol] = []
    private var keyMonitor: Any?

    func attach(_ window: NSWindow) {
        guard self.window !== window else { return }
        detach()
        self.window = window
        window.collectionBehavior.insert(.fullScreenPrimary)
        let center = NotificationCenter.default
        for (name, fullScreen) in [
            (NSWindow.willEnterFullScreenNotification, true),
            (NSWindow.didEnterFullScreenNotification, true),
            (NSWindow.willExitFullScreenNotification, false),
            (NSWindow.didExitFullScreenNotification, false)
        ] {
            observers.append(center.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.setFullScreen(fullScreen) }
            })
        }
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.window === self.window, self.isFullScreen, event.keyCode == 53 else { return event }
            self.toggleFullScreen()
            return nil
        }
        setFullScreen(window.styleMask.contains(.fullScreen))
    }
    func toggleFullScreen() { window?.toggleFullScreen(nil) }
    private func setFullScreen(_ value: Bool) {
        isFullScreen = value
        guard let window else { return }
        window.titleVisibility = value ? .hidden : .visible
        window.titlebarAppearsTransparent = value
        window.toolbar?.isVisible = !value
        for button in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            window.standardWindowButton(button)?.isHidden = value
        }
    }
    private func detach() {
        observers.forEach(NotificationCenter.default.removeObserver)
        observers.removeAll()
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor) }
        keyMonitor = nil
    }
    deinit {
        observers.forEach(NotificationCenter.default.removeObserver)
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor) }
    }
}

struct WindowAttachment: NSViewRepresentable {
    let presentation: WindowPresentation
    func makeNSView(context: Context) -> AttachmentView {
        let view = AttachmentView()
        view.didAttach = { [weak presentation] window in
            DispatchQueue.main.async { presentation?.attach(window) }
        }
        return view
    }
    func updateNSView(_ nsView: AttachmentView, context: Context) {}
    final class AttachmentView: NSView {
        var didAttach: ((NSWindow) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { didAttach?(window) }
        }
    }
}
