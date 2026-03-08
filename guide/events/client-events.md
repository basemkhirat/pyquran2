# Client Events {#client-events}

These are events your app emits (sends) to the server.

## 1. start_session {#start-session}

Starts a new recognition session for a specific verse range (can span multiple chapters).

### When to Emit

After connecting to the socket, when the user is ready to begin reciting.

### Payload

```typescript
{
  start_chapter_number: number;  // Starting surah number (1-114)
  start_verse_number: number;    // Starting ayah number in start chapter
  end_chapter_number: number;    // Ending surah number (1-114)
  end_verse_number: number;      // Ending ayah number in end chapter
}
```

### Example

::: code-group

```javascript [JavaScript]
socket.emit("start_session", {
  start_chapter_number: 1,    // Al-Fatiha
  start_verse_number: 1,      // First verse
  end_chapter_number: 1,      // Same chapter (or different for cross-chapter)
  end_verse_number: 7,        // Last verse
});
```

```swift [Swift]
socket.emit("start_session", [
    "start_chapter_number": 1,
    "start_verse_number": 1,
    "end_chapter_number": 1,
    "end_verse_number": 7,
])
```

```kotlin [Kotlin]
val payload = JSONObject().apply {
    put("start_chapter_number", 1)
    put("start_verse_number", 1)
    put("end_chapter_number", 1)
    put("end_verse_number", 7)
}
socket.emit("start_session", payload)
```

:::

### Server Response

The server responds with [`session_started`](/events/server-events#session-started) on success, or [`session_error`](/events/server-events#session-error) if the range is invalid.

### Notes

- Wait for `session_started` before streaming audio
- The server loads the words for the specified range and prepares the recognition pipeline
- Ranges can span multiple chapters (e.g., from Al-Fatiha verse 1 to Al-Baqarah verse 5)


## 2. audio_chunk {#audio-chunk}

Streams audio data to the server for recognition.

### When to Emit

Continuously while the user is speaking.

### Payload

**Binary data** - Raw PCM audio bytes (not JSON).

| Property | Value |
|----------|-------|
| Format | PCM 16-bit signed integer |
| Byte order | Little-endian |
| Channels | Mono (1 channel) |
| Sample rate | 16,000 Hz |
| Chunk duration | ~100-200ms recommended |

### Example

::: code-group

```javascript [JavaScript]
// audioBuffer is an ArrayBuffer containing Int16 PCM samples
socket.emit("audio_chunk", audioBuffer);
```

```swift [Swift]
// audioData is Data containing Int16 PCM samples
socket.emit("audio_chunk", audioData)
```

```kotlin [Kotlin]
// audioBytes is ByteArray containing Int16 PCM samples
socket.emit("audio_chunk", audioBytes)
```

:::

### Convert Float32 to Int16

Audio APIs often provide Float32 samples (-1.0 to 1.0). Convert to Int16:

```javascript
function floatTo16BitPCM(float32Array) {
  const int16Array = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = float32Array[i];
    int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16Array.buffer;
}

const pcmBuffer = floatTo16BitPCM(audioData);
socket.emit("audio_chunk", pcmBuffer);
```

### Chunk Size Calculation

For 16 kHz sample rate and 150ms chunks:

```
samples = 16000 * 0.150 = 2400 samples
bytes = 2400 * 2 = 4800 bytes per chunk
```

### Server Response

The server processes audio through VAD (Voice Activity Detection). When speech is detected and processed, it emits [`word_result`](/events/server-events#word-result) events.

### Notes

- Send chunks continuously while recording, don't wait for responses
- The server handles silence detection automatically
- No WAV header required - send raw PCM bytes only
- See [Audio Streaming Guide](/audio/streaming) for platform-specific capture code

---

## 3. stop_session {#stop-session}

Signals the end of the current session.

### When to Emit

When the user stops recording or wants to end the session.

### Payload

None (empty).

### Example

::: code-group

```javascript [JavaScript]
socket.emit("stop_session");
```

```swift [Swift]
socket.emit("stop_session")
```

```kotlin [Kotlin]
socket.emit("stop_session")
```

:::

### Server Response

The server:
1. Flushes any remaining audio in the VAD buffer
2. Processes any pending speech segment
3. May emit final [`word_result`](/events/server-events#word-result) event(s)
4. Emits [`session_stopped`](/events/server-events#session-stopped)

### Notes

- Stop your audio capture after emitting this event
- The session cannot be resumed; start a new session to continue


## 4. skip_word {#skip-word}

Skips the current word without recognition.

### When to Emit

When the user wants to skip a word they cannot pronounce or wants to move forward.

### Payload

None (empty).

### Example

::: code-group

```javascript [JavaScript]
socket.emit("skip_word");
```

```swift [Swift]
socket.emit("skip_word")
```

```kotlin [Kotlin]
socket.emit("skip_word")
```

:::

### Server Response

The server emits [`word_result`](/events/server-events#word-result) with `status: "skipped"` and advances to the next word.

```javascript
// Expected response
{
  chapter_number: 1,
  verse_number: 1,
  word_number: 3,
  status: "skipped"
}
```

### Notes

- The skip is immediate; the server resets its VAD buffer
- If the current word was the last word, `session_stopped` will follow


## Event Emission Summary

| Event | Payload Type | When to Use |
|-------|--------------|-------------|
| `start_session` | JSON object | After connecting, before recording |
| `audio_chunk` | Binary (ArrayBuffer/Data/ByteArray) | Continuously while recording |
| `stop_session` | None | When user stops recording |
| `skip_word` | None | When user taps skip button |
