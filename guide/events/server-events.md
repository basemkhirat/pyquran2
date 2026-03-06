# Server Events

These are events your app receives (listens for) from the server.

## 1. session_started

Confirms that the session has been initialized and is ready for audio streaming.

### When Received

After emitting `start_session`, when the server has loaded the words and prepared the recognition pipeline.

### Payload

```typescript
{} // Empty object
```

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("session_started", () => {
  console.log("Session ready, starting audio capture");
  startAudioRecording();
});
```

```swift [Swift]
socket.on("session_started") { data, ack in
    print("Session ready, starting audio capture")
    self.startAudioRecording()
}
```

```kotlin [Kotlin]
socket.on("session_started") {
    println("Session ready, starting audio capture")
    startAudioRecording()
}
```

```dart [Dart]
socket.on('session_started', (_) {
  print('Session ready, starting audio capture');
  startAudioRecording();
});
```

:::

### Notes

- Wait for this event before starting to stream audio
- If you don't receive this event, check for `session_error` event

---

## 2. verse_detected

Sent when the server identifies which verse the user started reciting from (start verse detection). Use this to set the current position in your UI so highlighting matches where the user is.

### When Received

- When the server is in a detecting phase and the user's first utterance matches the beginning of a verse in the requested range.

### Payload

```typescript
{
  verse_number: number;   // Ayah number that was detected
  word_index: number;     // Index into the session word list where that verse starts
  score: number;          // Confidence score (0–1)
}
```

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("verse_detected", (data) => {
  const { verse_number, word_index, score } = data;
  setCurrentWordIndex(word_index);  // Sync UI to detected verse start
});
```

```swift [Swift]
socket.on("verse_detected") { data, ack in
    guard let dict = data.first as? [String: Any],
          let wordIndex = dict["word_index"] as? Int else { return }
    setCurrentWordIndex(wordIndex)
}
```

```kotlin [Kotlin]
socket.on("verse_detected") { args ->
    val data = args[0] as JSONObject
    val wordIndex = data.getInt("word_index")
    setCurrentWordIndex(wordIndex)
}
```

```dart [Dart]
socket.on('verse_detected', (data) {
  final wordIndex = data['word_index'];
  setCurrentWordIndex(wordIndex);
});
```

:::

### Notes

- Only sent when the session uses start verse detection (e.g. when the server started with a detecting phase). After this event, the server sends `word_result` for subsequent words.
- Use `word_index` to set the current word/verse position in your UI.

---

## 3. verse_detection_failed

Sent when the server could not identify which verse the user started from.

### When Received

- When the server is in a detecting phase and the user's utterance did not match the start of any verse in the range (typically after the user stops speaking).

### Payload

```typescript
{} // Empty object
```

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("verse_detection_failed", () => {
  showMessage("Verse not recognized, try again");
});
```

```swift [Swift]
socket.on("verse_detection_failed") { _, _ in
    showMessage("Verse not recognized, try again")
}
```

```kotlin [Kotlin]
socket.on("verse_detection_failed") {
    showMessage("Verse not recognized, try again")
}
```

```dart [Dart]
socket.on('verse_detection_failed', (_) {
  showMessage('Verse not recognized, try again');
});
```

:::

### Notes

- The server keeps listening; the user can try again by speaking the beginning of a verse in the range.
- Optional: show a short message (e.g. "Verse not recognized, try again") without stopping the session.

---

## 4. word_result

Contains the recognition result for a single word.

### When Received

- When the server successfully recognizes a word from the audio
- When you emit `skip_word`
- When processing the final audio segment after `stop_session`

### Payload

```typescript
{
  chapter_number: number;   // Surah number
  verse_number: number;     // Ayah number
  word_number: number;      // Word index within the verse (0-based)
  status: "correct" | "incorrect" | "skipped";
}
```

### Status Values

| Status | Meaning | UI Suggestion |
|--------|---------|---------------|
| `correct` | Word was pronounced correctly | Green highlight, advance to next |
| `incorrect` | Word was not recognized correctly | Red highlight, may retry |
| `skipped` | Word was skipped by user | Gray/neutral highlight, advance |

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("word_result", (data) => {
  const { chapter_number, verse_number, word_number, status } = data;
  
  console.log(`Word ${word_number} in ${chapter_number}:${verse_number} - ${status}`);
  
  switch (status) {
    case "correct":
      highlightWord(word_number, "green");
      break;
    case "incorrect":
      highlightWord(word_number, "red");
      break;
    case "skipped":
      highlightWord(word_number, "gray");
      break;
  }
});
```

```swift [Swift]
socket.on("word_result") { data, ack in
    guard let dict = data.first as? [String: Any],
          let chapterNumber = dict["chapter_number"] as? Int,
          let verseNumber = dict["verse_number"] as? Int,
          let wordNumber = dict["word_number"] as? Int,
          let status = dict["status"] as? String else {
        return
    }
    
    print("Word \(wordNumber) in \(chapterNumber):\(verseNumber) - \(status)")
    
    DispatchQueue.main.async {
        self.updateWordStatus(wordNumber: wordNumber, status: status)
    }
}
```

```kotlin [Kotlin]
socket.on("word_result") { args ->
    val data = args[0] as JSONObject
    val chapterNumber = data.getInt("chapter_number")
    val verseNumber = data.getInt("verse_number")
    val wordNumber = data.getInt("word_number")
    val status = data.getString("status")
    
    println("Word $wordNumber in $chapterNumber:$verseNumber - $status")
    
    runOnUiThread {
        updateWordStatus(wordNumber, status)
    }
}
```

```dart [Dart]
socket.on('word_result', (data) {
  final chapterNumber = data['chapter_number'];
  final verseNumber = data['verse_number'];
  final wordNumber = data['word_number'];
  final status = data['status'];
  
  print('Word $wordNumber in $chapterNumber:$verseNumber - $status');
  
  setState(() {
    updateWordStatus(wordNumber, status);
  });
});
```

:::

### Notes

- Results arrive in order as words are recognized
- For `incorrect` status, the server does NOT automatically advance - the user may retry
- For `correct` status, the server advances to the next word
- Multiple `word_result` events may be emitted in succession if the user speaks multiple words

---

## 5. session_stopped

Signals that the session has ended.

### When Received

- After all words in the range have been processed
- After emitting `stop_session` and final processing completes
- When the session ends for any other reason

### Payload

```typescript
{} // Empty object
```

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("session_stopped", () => {
  console.log("Session complete");
  stopAudioRecording();
});
```

```swift [Swift]
socket.on("session_stopped") { data, ack in
    print("Session complete")
    self.stopAudioRecording()
}
```

```kotlin [Kotlin]
socket.on("session_stopped") {
    println("Session complete")
    stopAudioRecording()
}
```

```dart [Dart]
socket.on('session_stopped', (_) {
  print('Session complete');
  stopAudioRecording();
});
```

:::

### Notes

- Always stop audio recording when you receive this event
- You may start a new session by emitting `start_session` again

---

## 6. session_error

Indicates an error occurred during the session.

### When Received

- When `start_session` references an invalid range
- When the session state is invalid (e.g., not connected)
- When an internal server error occurs

### Payload

```typescript
{
  reason?: string;  // Error description
}
```

### Known Reason Values

| Reason | Meaning |
|--------|---------|
| `not_connected` | Session was not properly initialized |
| (other) | Server-specific error messages |

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("session_error", (data) => {
  const reason = data?.reason || "Unknown error";
  console.error("Session error:", reason);
  
  stopAudioRecording();
  showErrorMessage(reason);
});
```

```swift [Swift]
socket.on("session_error") { data, ack in
    let reason = (data.first as? [String: Any])?["reason"] as? String ?? "Unknown error"
    print("Session error: \(reason)")
    
    self.stopAudioRecording()
    self.showErrorMessage(reason)
}
```

```kotlin [Kotlin]
socket.on("session_error") { args ->
    val data = args.firstOrNull() as? JSONObject
    val reason = data?.optString("reason") ?: "Unknown error"
    
    println("Session error: $reason")
    
    runOnUiThread {
        stopAudioRecording()
        showErrorMessage(reason)
    }
}
```

```dart [Dart]
socket.on('session_error', (data) {
  final reason = data?['reason'] ?? 'Unknown error';
  print('Session error: $reason');
  
  stopAudioRecording();
  showErrorMessage(reason);
});
```

:::

### Notes

- Always handle this event to provide user feedback
- Stop audio recording if active
- Consider offering a retry option

---

## Event Handling Summary

| Event | Payload | Action to Take |
|-------|---------|----------------|
| `session_started` | `{}` | Start audio recording |
| `word_result` | Word details + status | Update UI, track progress |
| `verse_detected` | verse_number, word_index, score | Set current position to word_index |
| `verse_detection_failed` | `{}` | Optional: show "try again" message |
| `session_stopped` | `{}` | Stop recording |
| `session_error` | `{ reason }` | Stop recording, show error |

## Complete Event Listener Setup

```javascript
function setupSocketListeners(socket) {
  socket.on("session_started", () => {
    startAudioRecording();
  });

  socket.on("word_result", (data) => {
    updateWordResult(data);
  });

  socket.on("verse_detected", (data) => {
    setCurrentWordIndex(data.word_index);
  });

  socket.on("verse_detection_failed", () => {
    showMessage("Verse not recognized, try again");
  });

  socket.on("session_stopped", () => {
    stopAudioRecording();
  });

  socket.on("session_error", (data) => {
    stopAudioRecording();
    showError(data.reason);
  });
}
```
