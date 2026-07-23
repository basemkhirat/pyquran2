# Sequence Diagrams

Visual representations of the Socket.IO communication flow.

## Basic Session Flow

The standard flow for a complete recognition session:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    App->>Server: connect()
    Server-->>App: connected

    App->>Server: start_session({start_chapter_number, start_verse_number, end_chapter_number, end_verse_number[, score_threshold, mode, record]})
    Server-->>App: session_started

    Note over App: Start audio capture

    loop While user speaks
        App->>Server: audio_chunk(binary PCM)
        Note over Server: VAD + Whisper processing
        Server-->>App: word_result({chapter_number, verse_number, word_number, status, total_score})
    end

    App->>Server: stop_session
    Server-->>App: word_result (final, if any)
    Server-->>App: session_stopped

    Note over App: Stop audio capture

    opt record: true
        Note over Server: Close + finalize recording.wav
        Server-->>App: session_ended({id, type, duration, url, words, ...})
    end
```

## Session with Skip

Flow when the user skips a word:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    Note over App,Server: Session already started...

    App->>Server: audio_chunk(binary)
    Server-->>App: word_result({word_number, status: correct})

    App->>Server: audio_chunk(binary)
    Note over App: User has difficulty with current word

    App->>Server: skip_word
    Server-->>App: word_result({word_number, status: skipped})

    App->>Server: audio_chunk(binary)
    Server-->>App: word_result({word_number, status: correct})
```

## Session with Error

Flow when an error occurs:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    App->>Server: connect()
    Server-->>App: connected

    App->>Server: start_session({invalid range, e.g. start > end})
    Server-->>App: session_error({reason: "invalid_range"})

    Note over App: Show error, allow retry
```

## Authentication Flow

Authentication is required (enabled by default). Connection with API key:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    App->>Server: connect({auth: {api_key: "secret"}})

    alt Valid API key
        Server-->>App: connected
        Note over App: Proceed with session
    else Invalid API key
        Server-->>App: connect_error("authentication_failed")
        Note over App: Show error, check API key
    end
```

## Complete Session Lifecycle

Full lifecycle including connection management:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    Note over App: User opens recording screen

    App->>Server: connect()
    Server-->>App: connected

    Note over App: User taps Start

    App->>Server: start_session({start_chapter_number: 1, start_verse_number: 1, end_chapter_number: 1, end_verse_number: 7})
    Server-->>App: session_started

    Note over App: Start microphone capture

    loop Audio streaming
        App->>Server: audio_chunk(~150ms PCM)
    end

    loop Recognition results
        Server-->>App: word_result({chapter_number, verse_number, word_number, status, total_score})
        Note over App: Update UI
    end

    alt All words complete
        Server-->>App: session_stopped
    else User taps Stop
        App->>Server: stop_session
        Server-->>App: word_result (final, if any)
        Server-->>App: session_stopped
    end

    Note over App: Stop microphone

    Note over App: User leaves screen

    App->>Server: disconnect()
    Server-->>App: disconnected
```

## Audio Processing Pipeline

Internal server flow (for reference):

```mermaid
flowchart TB
    A[audio_chunk received] --> B[VAD Processor]
    B --> C{Speech detected?}
    C -->|No| D[Buffer audio]
    D --> B
    C -->|Yes| E[Accumulate speech]
    E --> F{Silence detected?}
    F -->|No| B
    F -->|Yes| G[Extract speech segment]
    G --> H[Whisper transcription]
    H --> I[Score words]
    I --> J[Emit word_result]
    J --> K{More words?}
    K -->|Yes| B
    K -->|No| L[Emit session_stopped]
```

## State Diagram

Client-side state machine:

```mermaid
stateDiagram-v2
    [*] --> Disconnected

    Disconnected --> Connecting: connect()
    Connecting --> Connected: connected event
    Connecting --> Disconnected: connect_error

    Connected --> SessionActive: session_started
    Connected --> Disconnected: disconnect()

    SessionActive --> SessionActive: word_result
    SessionActive --> Connected: session_stopped
    SessionActive --> Connected: session_error
    SessionActive --> Disconnected: disconnect event

    Connected --> Disconnected: disconnect()
```

## Multi-Word Recognition

How multiple words are processed in sequence:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    Note over App,Server: Session for verses with 5 words

    App->>Server: audio_chunk (word 1 spoken)
    Server-->>App: word_result({word_number, status: correct})

    App->>Server: audio_chunk (word 2 spoken)
    Server-->>App: word_result({word_number, status: incorrect})

    Note over App: User retries word 2

    App->>Server: audio_chunk (word 2 retry)
    Server-->>App: word_result({word_number, status: correct})

    App->>Server: audio_chunk (words 3-4 spoken quickly)
    Server-->>App: word_result({word_number, status: correct})
    Server-->>App: word_result({word_number, status: correct})

    App->>Server: audio_chunk (word 5 spoken)
    Server-->>App: word_result({word_number, status: correct})
    Server-->>App: session_stopped
```

## Reconnection Handling

Handling disconnections gracefully:

```mermaid
sequenceDiagram
    participant App as Mobile_App
    participant Server as Backend

    Note over App,Server: Session in progress...

    Server--xApp: disconnect (network issue)

    Note over App: Stop audio capture
    Note over App: Show reconnecting UI

    App->>Server: connect() (auto-reconnect)
    Server-->>App: connected

    Note over App: Session state was lost
    Note over App: Prompt user to restart

    App->>Server: start_session({...})
    Server-->>App: session_started

    Note over App: Resume audio capture
```
