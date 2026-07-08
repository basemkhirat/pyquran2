# Server Events {#server-events}

These are events your app receives (listens for) from the server.

## 1. session_started {#session-started}

Confirms that the session has been initialized and is ready for audio streaming.

### When Received

After emitting `start_session`, when the server has loaded the words and prepared the recognition pipeline.

### Payload

```typescript
{
  id: string;  // Unique session id
}
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

:::

### Notes

- Wait for this event before starting to stream audio
- If you don't receive this event, check for `session_error` event

---

## 2. verse_detected {#verse-detected}

Sent when the server identifies which verse the user started reciting from (start verse detection). Use this to set the current position in your UI so highlighting matches where the user is.

### When Received

- When the server is in the detecting phase and the user's opening recitation is confidently matched to a verse in the requested range.

### Payload

```typescript
{
  chapter_number: number; // Surah number where the verse was detected
  verse_number: number;   // Ayah number that was detected
  word_number: number;    // Position of the word within the verse (same as word_result)
}
```

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("verse_detected", (data) => {
  const { chapter_number, verse_number, word_number } = data;
  const idx = words.findIndex(
    (w) => w.surah === chapter_number && w.ayah === verse_number && w.word_index === word_number
  );
  if (idx !== -1) setCurrentWordIndex(idx);  // Sync UI to detected verse start
});
```

```swift [Swift]
socket.on("verse_detected") { data, ack in
    guard let dict = data.first as? [String: Any],
          let chapterNumber = dict["chapter_number"] as? Int,
          let verseNumber = dict["verse_number"] as? Int,
          let wordNumber = dict["word_number"] as? Int,
          let idx = words.firstIndex(where: { $0.surah == chapterNumber && $0.ayah == verseNumber && $0.word_index == wordNumber }) else { return }
    print("Detected \(chapterNumber):\(verseNumber)")
    setCurrentWordIndex(idx)
}
```

```kotlin [Kotlin]
socket.on("verse_detected") { args ->
    val data = args[0] as JSONObject
    val chapterNumber = data.getInt("chapter_number")
    val verseNumber = data.getInt("verse_number")
    val wordNumber = data.getInt("word_number")
    val idx = words.indexOfFirst { it.surah == chapterNumber && it.ayah == verseNumber && it.wordIndex == wordNumber }
    if (idx != -1) setCurrentWordIndex(idx)
    println("Detected $chapterNumber:$verseNumber")
}
```

:::

### Notes

- Sent **once** per session, when the server identifies the start verse from the user's opening recitation. The whole utterance is matched against the selected range, so the user may begin at any verse in the range — not only the first.
- **Repeated / identical verses:** when the opening matches several verses that are textually identical (e.g. Al-Rahman's refrain `فبأي آلاء ربكما تكذبان`, which repeats many times), the server does **not** guess. It waits until the recitation continues into the next distinct verse (or the user pauses) before committing — so detection can take a moment longer for repeated verses.
- After this event, the words the user already recited are scored right away (you receive `word_result` for them), and the session keeps emitting `word_result` for the following words.
---

## 3. verse_detection_failed {#verse-detection-failed}

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

:::

### Notes

- The server keeps listening; the user can try again by speaking the beginning of a verse in the range.
- Optional: show a short message (e.g. "Verse not recognized, try again") without stopping the session.

---

## 4. word_result {#word-result}

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
  total_score: number;      // Overall score 0–1 — always present (0 for skipped). See Score below.
  is_interim?: boolean;     // Present only during live streaming — see below
}
```

### Status Values

| Status | Meaning | UI Suggestion |
|--------|---------|---------------|
| `correct` | Word was pronounced correctly | Green highlight, advance to next |
| `incorrect` | Word was not recognized correctly | Red highlight, may retry |
| `skipped` | Word was skipped by user | Gray/neutral highlight, advance |

### Interim vs. Confirmed Results {#interim-results}

While the user is still speaking, the server streams **interim** (preliminary) results so the UI can react live. These carry an `is_interim` flag:

| `is_interim` | Meaning | UI Suggestion |
|--------------|---------|---------------|
| `true` | Preliminary result for the word being spoken now — **may be revised** by a later `word_result`. | Render word with (e.g. a lighter highlight or loading). |
| `false` | Confirmed — the server has advanced past this word. | Apply the final highlight. |

### Example Handler

::: code-group

```javascript [JavaScript]
socket.on("word_result", (data) => {
  const { chapter_number, verse_number, word_number, status, total_score } = data;
  const percent = Math.round(total_score * 100);  // always available (0 for skipped)

  console.log(`Word ${word_number} in ${chapter_number}:${verse_number} - ${status} (${percent}%)`);

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

:::

### Notes

- Results arrive in order as words are recognized
- For `incorrect` status, the server does NOT automatically advance - the user may retry
- For `correct` status, the server advances to the next word
- Multiple `word_result` events may be emitted in succession if the user speaks multiple words
- Handle `is_interim` (see above) so a preliminary result isn't shown as final — an interim word may be revised by a following `word_result`
- `total_score` (see [Score](#word-score)) is **always** included, independent of `SEND_WORD_RESULT_DETAILS` — use it for a per-word percentage.
- When the server has `SEND_WORD_RESULT_DETAILS` enabled, each result also carries extra diagnostic fields (`expected`, `transcribed`, `char_score`, `diacritic_score`, `text_score`, `acoustic_score`, `acoustic_char`, `acoustic_diacritic`, `acoustic_decoded`). They are safe to ignore in production.

---

## 5. session_stopped {#session-stopped}

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

:::

### Notes

- Always stop audio recording when you receive this event
- You may start a new session by emitting `start_session` again

---

## 6. session_error {#session-error}

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

:::

### Notes

- Always handle this event to provide user feedback
- Stop audio recording if active
- Consider offering a retry option

---

## Event Handling Summary

| Event | Payload | Action to Take |
|-------|---------|----------------|
| `session_started` | `{ id }` | Start audio recording |
| `word_result` | Word details + status | Update UI, track progress |
| `verse_detected` | chapter_number, verse_number, word_number | Find word index, set current position |
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
    const idx = words.findIndex(
      (w) => w.surah === data.chapter_number && w.ayah === data.verse_number && w.word_index === data.word_number
    );
    if (idx !== -1) setCurrentWordIndex(idx);
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
