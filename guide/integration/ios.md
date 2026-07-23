# iOS Integration

Complete guide for integrating Quran voice recognition into an iOS app using Swift.

## Prerequisites

- iOS 13.0+
- Xcode 14+
- CocoaPods or Swift Package Manager

## Installation

### CocoaPods

Add to your `Podfile`:

```ruby
pod 'Socket.IO-Client-Swift', '~> 16.0'
```

Then run:

```bash
pod install
```

### Swift Package Manager

Add the package URL:
```
https://github.com/socketio/socket.io-client-swift
```

## Project Setup

### 1. Microphone Permission

Add to `Info.plist`:

```xml
<key>NSMicrophoneUsageDescription</key>
<string>This app needs microphone access to recognize your Quran recitation.</string>
```

### 2. Background Audio (Optional)

If you need recording to continue in background, add to `Info.plist`:

```xml
<key>UIBackgroundModes</key>
<array>
    <string>audio</string>
</array>
```

## Complete Implementation

### SocketManager.swift

```swift
import Foundation
import SocketIO

class QuranSocketManager {
    static let shared = QuranSocketManager()
    
    private let manager: SocketManager
    let socket: SocketIOClient
    
    // Configuration (authentication is enabled by default)
    private let serverURL = "https://websocket.zekr.online"
    private let apiKey = "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"
    
    private init() {
        let config: SocketIOClientConfiguration = [
            .log(true),
            .compress,
            .forceWebsockets(true),
            .connectParams(["api_key": apiKey])
        ]
        
        manager = SocketManager(
            socketURL: URL(string: serverURL)!,
            config: config
        )
        
        socket = manager.defaultSocket
    }
    
    func connect() {
        socket.connect()
    }
    
    func disconnect() {
        socket.disconnect()
    }
}
```

### AudioRecorder.swift

```swift
import AVFoundation
import SocketIO

protocol AudioRecorderDelegate: AnyObject {
    func audioRecorderDidUpdateVolume(_ volume: Float)
}

class AudioRecorder {
    weak var delegate: AudioRecorderDelegate?
    
    private let audioEngine = AVAudioEngine()
    private let socket: SocketIOClient
    
    private let sampleRate: Double = 16000
    private let chunkDurationMs: Double = 150
    
    private var isRecording = false
    
    init(socket: SocketIOClient) {
        self.socket = socket
    }
    
    func startRecording() throws {
        guard !isRecording else { return }
        
        // Configure audio session
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker])
        try session.setPreferredSampleRate(sampleRate)
        try session.setActive(true)
        
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        
        // Target format: 16-bit PCM, 16kHz, mono
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: sampleRate,
            channels: 1,
            interleaved: true
        ) else {
            throw AudioRecorderError.formatCreationFailed
        }
        
        // Create converter if needed
        let converter = AVAudioConverter(from: inputFormat, to: outputFormat)
        
        let bufferSize = AVAudioFrameCount(sampleRate * chunkDurationMs / 1000)
        
        inputNode.installTap(onBus: 0, bufferSize: bufferSize, format: inputFormat) { [weak self] buffer, time in
            self?.processBuffer(buffer, converter: converter, outputFormat: outputFormat)
        }
        
        try audioEngine.start()
        isRecording = true
    }
    
    func stopRecording() {
        guard isRecording else { return }
        
        audioEngine.inputNode.removeTap(onBus: 0)
        audioEngine.stop()
        
        try? AVAudioSession.sharedInstance().setActive(false)
        
        isRecording = false
    }
    
    private func processBuffer(_ buffer: AVAudioPCMBuffer, converter: AVAudioConverter?, outputFormat: AVAudioFormat) {
        // Calculate volume for UI feedback
        if let channelData = buffer.floatChannelData?[0] {
            let frameLength = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<frameLength {
                sum += abs(channelData[i])
            }
            let volume = sum / Float(frameLength)
            DispatchQueue.main.async {
                self.delegate?.audioRecorderDidUpdateVolume(volume)
            }
        }
        
        // Convert to output format
        guard let converter = converter else {
            // No conversion needed, send directly
            if let int16Data = buffer.int16ChannelData {
                let data = Data(bytes: int16Data[0], count: Int(buffer.frameLength) * 2)
                socket.emit("audio_chunk", data)
            }
            return
        }
        
        let outputBuffer = AVAudioPCMBuffer(
            pcmFormat: outputFormat,
            frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * sampleRate / buffer.format.sampleRate)
        )!
        
        var error: NSError?
        converter.convert(to: outputBuffer, error: &error) { inNumPackets, outStatus in
            outStatus.pointee = .haveData
            return buffer
        }
        
        if error == nil, let int16Data = outputBuffer.int16ChannelData {
            let data = Data(bytes: int16Data[0], count: Int(outputBuffer.frameLength) * 2)
            socket.emit("audio_chunk", data)
        }
    }
    
    enum AudioRecorderError: Error {
        case formatCreationFailed
    }
}
```

### RecitationSession.swift

```swift
import Foundation
import SocketIO

struct WordResult {
    let chapterNumber: Int
    let verseNumber: Int
    let wordNumber: Int
    let status: WordStatus
    
    enum WordStatus: String {
        case correct
        case incorrect
        case skipped
    }
}

protocol RecitationSessionDelegate: AnyObject {
    func sessionDidStart()
    func sessionDidReceiveWordResult(_ result: WordResult)
    func sessionDidStop()
    func sessionDidError(_ reason: String)
    /// Recorded sessions only (`record: true`) — see the `session_ended` event.
    /// `durationMs` and each word's start/end times are milliseconds.
    func sessionDidFinishRecording(url: URL, durationMs: Int, words: [[String: Any]])
}

// Default no-op, so implementing it is optional: sessions started without
// `record: true` never receive `session_ended`.
extension RecitationSessionDelegate {
    func sessionDidFinishRecording(url: URL, durationMs: Int, words: [[String: Any]]) {}
}

class RecitationSession {
    weak var delegate: RecitationSessionDelegate?
    
    private let socketManager = QuranSocketManager.shared
    private let audioRecorder: AudioRecorder
    
    private var isSessionActive = false
    
    init() {
        self.audioRecorder = AudioRecorder(socket: socketManager.socket)
        setupSocketListeners()
    }
    
    private func setupSocketListeners() {
        let socket = socketManager.socket
        
        socket.on(clientEvent: .connect) { [weak self] _, _ in
            print("Connected to server")
        }
        
        socket.on(clientEvent: .disconnect) { [weak self] _, _ in
            print("Disconnected from server")
            self?.audioRecorder.stopRecording()
        }
        
        socket.on("session_started") { [weak self] _, _ in
            self?.isSessionActive = true
            self?.delegate?.sessionDidStart()
            
            do {
                try self?.audioRecorder.startRecording()
            } catch {
                print("Failed to start recording: \(error)")
            }
        }
        
        socket.on("word_result") { [weak self] data, _ in
            guard let dict = data.first as? [String: Any],
                  let chapterNumber = dict["chapter_number"] as? Int,
                  let verseNumber = dict["verse_number"] as? Int,
                  let wordNumber = dict["word_number"] as? Int,
                  let statusString = dict["status"] as? String,
                  let status = WordResult.WordStatus(rawValue: statusString) else {
                return
            }
            
            let result = WordResult(
                chapterNumber: chapterNumber,
                verseNumber: verseNumber,
                wordNumber: wordNumber,
                status: status
            )
            
            self?.delegate?.sessionDidReceiveWordResult(result)
        }
        
        socket.on("session_stopped") { [weak self] _, _ in
            self?.isSessionActive = false
            self?.audioRecorder.stopRecording()
            self?.delegate?.sessionDidStop()
        }

        // Recorded sessions only. Arrives after session_stopped, once the server has
        // closed the WAV — don't fetch the recording before this.
        socket.on("session_ended") { [weak self] data, _ in
            // Every session emits this; url is null when it wasn't recorded — the guard then
            // skips the recording callback (nothing to play back).
            guard let dict = data.first as? [String: Any],
                  let urlString = dict["url"] as? String,
                  let url = URL(string: urlString) else {
                return
            }
            let durationMs = dict["duration"] as? Int ?? 0
            let words = dict["words"] as? [[String: Any]] ?? []
            self?.delegate?.sessionDidFinishRecording(url: url, durationMs: durationMs, words: words)
        }
        
        socket.on("session_error") { [weak self] data, _ in
            let reason = (data.first as? [String: Any])?["reason"] as? String ?? "Unknown error"
            self?.isSessionActive = false
            self?.audioRecorder.stopRecording()
            self?.delegate?.sessionDidError(reason)
        }
        
    }
    
    func connect() {
        socketManager.connect()
    }
    
    func disconnect() {
        stopSession()
        socketManager.disconnect()
    }
    
    func startSession(startChapter: Int, startVerse: Int, endChapter: Int, endVerse: Int) {
        let socket = socketManager.socket
        
        if !socket.status.active {
            socket.once(clientEvent: .connect) { [weak self] _, _ in
                self?.emitStartSession(startChapter: startChapter, startVerse: startVerse, endChapter: endChapter, endVerse: endVerse)
            }
            socket.connect()
        } else {
            emitStartSession(startChapter: startChapter, startVerse: startVerse, endChapter: endChapter, endVerse: endVerse)
        }
    }
    
    private func emitStartSession(startChapter: Int, startVerse: Int, endChapter: Int, endVerse: Int) {
        socketManager.socket.emit("start_session", [
            "start_chapter_number": startChapter,
            "start_verse_number": startVerse,
            "end_chapter_number": endChapter,
            "end_verse_number": endVerse,
            // "score_threshold": 0.6,  // optional (0-1); omit to use server default
            // "mode": "continuous",    // optional; "word_by_word" (default) or "continuous"
            // "record": true,          // optional; persist this session server-side
        ])
    }
    
    func stopSession() {
        guard isSessionActive else { return }
        
        audioRecorder.stopRecording()
        socketManager.socket.emit("stop_session")
    }
    
    func skipWord() {
        guard isSessionActive else { return }
        socketManager.socket.emit("skip_word")
    }
}
```

### Usage in ViewController

```swift
import UIKit

class RecitationViewController: UIViewController {
    
    private let session = RecitationSession()
    
    @IBOutlet weak var statusLabel: UILabel!
    @IBOutlet weak var recordButton: UIButton!
    @IBOutlet weak var skipButton: UIButton!
    
    override func viewDidLoad() {
        super.viewDidLoad()
        session.delegate = self
        session.connect()
    }
    
    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        session.disconnect()
    }
    
    @IBAction func recordButtonTapped(_ sender: UIButton) {
        // Start session for Al-Fatiha (chapter 1, verses 1-7)
        session.startSession(startChapter: 1, startVerse: 1, endChapter: 1, endVerse: 7)
        recordButton.isEnabled = false
    }
    
    @IBAction func stopButtonTapped(_ sender: UIButton) {
        session.stopSession()
    }
    
    @IBAction func skipButtonTapped(_ sender: UIButton) {
        session.skipWord()
    }
}

extension RecitationViewController: RecitationSessionDelegate {
    func sessionDidStart() {
        DispatchQueue.main.async {
            self.statusLabel.text = "Recording..."
            self.skipButton.isEnabled = true
        }
    }
    
    func sessionDidReceiveWordResult(_ result: WordResult) {
        DispatchQueue.main.async {
            let statusText: String
            switch result.status {
            case .correct:
                statusText = "✓ Correct"
            case .incorrect:
                statusText = "✗ Incorrect"
            case .skipped:
                statusText = "→ Skipped"
            }
            self.statusLabel.text = "Word \(result.wordNumber): \(statusText)"
        }
    }
    
    func sessionDidStop() {
        DispatchQueue.main.async {
            self.statusLabel.text = "Session complete"
            self.recordButton.isEnabled = true
            self.skipButton.isEnabled = false
        }
    }
    
    func sessionDidError(_ reason: String) {
        DispatchQueue.main.async {
            self.statusLabel.text = "Error: \(reason)"
            self.recordButton.isEnabled = true
        }
    }
    
}
```

## SwiftUI Version

```swift
import SwiftUI

struct RecitationView: View {
    @StateObject private var viewModel = RecitationViewModel()
    
    var body: some View {
        VStack(spacing: 20) {
            Text(viewModel.statusText)
                .font(.headline)
            
            HStack(spacing: 40) {
                Button("Start") {
                    viewModel.startSession(startChapter: 1, startVerse: 1, endChapter: 1, endVerse: 7)
                }
                .disabled(viewModel.isRecording)
                
                Button("Stop") {
                    viewModel.stopSession()
                }
                .disabled(!viewModel.isRecording)
                
                Button("Skip") {
                    viewModel.skipWord()
                }
                .disabled(!viewModel.isRecording)
            }
            
            // Word results list
            List(viewModel.wordResults, id: \.wordNumber) { result in
                HStack {
                    Text("Word \(result.wordNumber)")
                    Spacer()
                    Text(result.status.rawValue)
                        .foregroundColor(colorForStatus(result.status))
                }
            }
        }
        .onAppear {
            viewModel.connect()
        }
        .onDisappear {
            viewModel.disconnect()
        }
    }
    
    private func colorForStatus(_ status: WordResult.WordStatus) -> Color {
        switch status {
        case .correct: return .green
        case .incorrect: return .red
        case .skipped: return .gray
        }
    }
}

class RecitationViewModel: ObservableObject {
    @Published var statusText = "Ready"
    @Published var isRecording = false
    @Published var wordResults: [WordResult] = []
    
    private let session = RecitationSession()
    
    init() {
        session.delegate = self
    }
    
    func connect() {
        session.connect()
    }
    
    func disconnect() {
        session.disconnect()
    }
    
    func startSession(startChapter: Int, startVerse: Int, endChapter: Int, endVerse: Int) {
        wordResults = []
        session.startSession(startChapter: startChapter, startVerse: startVerse, endChapter: endChapter, endVerse: endVerse)
    }
    
    func stopSession() {
        session.stopSession()
    }
    
    func skipWord() {
        session.skipWord()
    }
}

extension RecitationViewModel: RecitationSessionDelegate {
    func sessionDidStart() {
        DispatchQueue.main.async {
            self.statusText = "Recording..."
            self.isRecording = true
        }
    }
    
    func sessionDidReceiveWordResult(_ result: WordResult) {
        DispatchQueue.main.async {
            self.wordResults.append(result)
        }
    }
    
    func sessionDidStop() {
        DispatchQueue.main.async {
            self.statusText = "Session complete"
            self.isRecording = false
        }
    }
    
    func sessionDidError(_ reason: String) {
        DispatchQueue.main.async {
            self.statusText = "Error: \(reason)"
            self.isRecording = false
        }
    }
    
}
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| No audio captured | Check microphone permission in Settings |
| Connection fails | Verify server URL and network connectivity |
| Poor recognition | Ensure 16kHz sample rate and quiet environment |
| Choppy audio | Reduce chunk duration or check CPU usage |

### Debug Logging

Enable Socket.IO logging:

```swift
// In SocketManager config
.log(true)
```

Log audio buffer info:

```swift
print("Buffer: \(buffer.frameLength) frames, format: \(buffer.format)")
```
