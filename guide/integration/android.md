# Android Integration

Complete guide for integrating Quran voice recognition into an Android app using Kotlin.

## Prerequisites

- Android SDK 21+ (Android 5.0 Lollipop)
- Android Studio
- Gradle

## Installation

Add to your app's `build.gradle`:

```groovy
dependencies {
    implementation 'io.socket:socket.io-client:2.1.0'
    implementation 'org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3'
}
```

For Java 8 compatibility, add:

```groovy
android {
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_1_8
        targetCompatibility JavaVersion.VERSION_1_8
    }
    kotlinOptions {
        jvmTarget = '1.8'
    }
}
```

## Project Setup

### 1. Permissions

Add to `AndroidManifest.xml`:

```xml
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.INTERNET" />
```

## Complete Implementation

### SocketManager.kt

```kotlin
package com.example.quran

import io.socket.client.IO
import io.socket.client.Socket
import org.json.JSONObject

object QuranSocketManager {
    
    private const val SERVER_URL = "https://websocket.zekr.online"
    // Authentication is enabled by default
    private const val API_KEY = "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"
    
    val socket: Socket by lazy {
        val options = IO.Options().apply {
            transports = arrayOf("websocket")
            forceNew = true
            reconnection = true
            auth = mapOf("api_key" to API_KEY)
        }
        
        IO.socket(SERVER_URL, options)
    }
    
    fun connect() {
        if (!socket.connected()) {
            socket.connect()
        }
    }
    
    fun disconnect() {
        socket.disconnect()
    }
}
```

### AudioRecorder.kt

```kotlin
package com.example.quran

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import io.socket.client.Socket
import kotlinx.coroutines.*
import kotlin.math.abs

class AudioRecorder(
    private val context: Context,
    private val socket: Socket
) {
    
    interface VolumeListener {
        fun onVolumeChanged(volume: Float)
    }
    
    var volumeListener: VolumeListener? = null
    
    private val sampleRate = 16000
    private val chunkDurationMs = 150
    
    private var audioRecord: AudioRecord? = null
    private var recordingJob: Job? = null
    private var isRecording = false
    
    private val bufferSize: Int
        get() {
            val minSize = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            val chunkSize = (sampleRate * chunkDurationMs / 1000) * 2
            return maxOf(minSize, chunkSize)
        }
    
    fun hasPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }
    
    fun startRecording() {
        if (isRecording) return
        if (!hasPermission()) {
            throw SecurityException("Microphone permission not granted")
        }
        
        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize
        )
        
        if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
            throw IllegalStateException("AudioRecord initialization failed")
        }
        
        audioRecord?.startRecording()
        isRecording = true
        
        recordingJob = CoroutineScope(Dispatchers.IO).launch {
            val chunkBytes = (sampleRate * chunkDurationMs / 1000) * 2
            val buffer = ShortArray(chunkBytes / 2)
            
            while (isActive && isRecording) {
                val shortsRead = audioRecord?.read(buffer, 0, buffer.size) ?: 0
                
                if (shortsRead > 0) {
                    // Calculate volume for UI
                    val volume = calculateVolume(buffer, shortsRead)
                    withContext(Dispatchers.Main) {
                        volumeListener?.onVolumeChanged(volume)
                    }
                    
                    // Convert to bytes and send
                    val byteBuffer = shortArrayToByteArray(buffer, shortsRead)
                    socket.emit("audio_chunk", byteBuffer)
                }
            }
        }
    }
    
    fun stopRecording() {
        if (!isRecording) return
        
        isRecording = false
        recordingJob?.cancel()
        recordingJob = null
        
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
    }
    
    private fun calculateVolume(buffer: ShortArray, length: Int): Float {
        var sum = 0L
        for (i in 0 until length) {
            sum += abs(buffer[i].toInt())
        }
        return (sum.toFloat() / length) / Short.MAX_VALUE
    }
    
    private fun shortArrayToByteArray(shorts: ShortArray, length: Int): ByteArray {
        val bytes = ByteArray(length * 2)
        for (i in 0 until length) {
            val value = shorts[i]
            bytes[i * 2] = (value.toInt() and 0xFF).toByte()
            bytes[i * 2 + 1] = (value.toInt() shr 8 and 0xFF).toByte()
        }
        return bytes
    }
}
```

### RecitationSession.kt

```kotlin
package com.example.quran

import android.content.Context
import io.socket.client.Socket
import org.json.JSONObject

data class WordResult(
    val chapterNumber: Int,
    val verseNumber: Int,
    val wordNumber: Int,
    val status: Status
) {
    enum class Status {
        CORRECT, INCORRECT, SKIPPED;
        
        companion object {
            fun fromString(value: String): Status {
                return when (value.lowercase()) {
                    "correct" -> CORRECT
                    "incorrect" -> INCORRECT
                    "skipped" -> SKIPPED
                    else -> throw IllegalArgumentException("Unknown status: $value")
                }
            }
        }
    }
}

interface RecitationSessionListener {
    fun onSessionStarted()
    fun onWordResult(result: WordResult)
    fun onSessionStopped()
    fun onSessionError(reason: String)

    /** Recorded sessions only (`record: true`) — see the `session_ended` event.
     *  Default no-op, so it is optional to implement. */
    fun onRecordingReady(recordingUrl: String, durationMs: Long, words: JSONArray) {}
}

class RecitationSession(context: Context) {
    
    var listener: RecitationSessionListener? = null
    
    private val socketManager = QuranSocketManager
    private val audioRecorder = AudioRecorder(context, socketManager.socket)
    
    private var isSessionActive = false
    
    init {
        setupSocketListeners()
    }
    
    private fun setupSocketListeners() {
        val socket = socketManager.socket
        
        socket.on(Socket.EVENT_CONNECT) {
            println("Connected to server")
        }
        
        socket.on(Socket.EVENT_DISCONNECT) {
            println("Disconnected from server")
            audioRecorder.stopRecording()
        }
        
        socket.on(Socket.EVENT_CONNECT_ERROR) { args ->
            val error = args.firstOrNull()
            println("Connection error: $error")
        }
        
        socket.on("session_started") {
            isSessionActive = true
            listener?.onSessionStarted()
            
            try {
                audioRecorder.startRecording()
            } catch (e: Exception) {
                println("Failed to start recording: ${e.message}")
            }
        }
        
        socket.on("word_result") { args ->
            val data = args.firstOrNull() as? JSONObject ?: return@on
            
            val result = WordResult(
                chapterNumber = data.getInt("chapter_number"),
                verseNumber = data.getInt("verse_number"),
                wordNumber = data.getInt("word_number"),
                status = WordResult.Status.fromString(data.getString("status"))
            )
            
            listener?.onWordResult(result)
        }
        
        socket.on("session_stopped") {
            isSessionActive = false
            audioRecorder.stopRecording()
            listener?.onSessionStopped()
        }

        // Recorded sessions only. Arrives after session_stopped, once the server has
        // closed the WAV — don't fetch the recording before this.
        socket.on("session_ended") { args ->
            val data = args.firstOrNull() as? JSONObject ?: return@on
            // Every session emits this; url is null when it wasn't recorded (nothing to
            // play back), so only fire the recording callback when a url is present.
            if (data.isNull("url")) return@on
            listener?.onRecordingReady(
                recordingUrl = data.getString("url"),
                durationMs = data.optLong("duration"),
                words = data.getJSONArray("words")
            )
        }
        
        socket.on("session_error") { args ->
            val data = args.firstOrNull() as? JSONObject
            val reason = data?.optString("reason") ?: "Unknown error"
            
            isSessionActive = false
            audioRecorder.stopRecording()
            listener?.onSessionError(reason)
        }
        
    }
    
    fun connect() {
        socketManager.connect()
    }
    
    fun disconnect() {
        stopSession()
        socketManager.disconnect()
    }
    
    fun startSession(startChapter: Int, startVerse: Int, endChapter: Int, endVerse: Int) {
        val socket = socketManager.socket
        
        if (!socket.connected()) {
            socket.once(Socket.EVENT_CONNECT) {
                emitStartSession(startChapter, startVerse, endChapter, endVerse)
            }
            socket.connect()
        } else {
            emitStartSession(startChapter, startVerse, endChapter, endVerse)
        }
    }
    
    private fun emitStartSession(startChapter: Int, startVerse: Int, endChapter: Int, endVerse: Int) {
        val payload = JSONObject().apply {
            put("start_chapter_number", startChapter)
            put("start_verse_number", startVerse)
            put("end_chapter_number", endChapter)
            put("end_verse_number", endVerse)
            // put("score_threshold", 0.6)  // optional (0-1); omit to use server default
            // put("mode", "continuous")    // optional; "word_by_word" (default) or "continuous"
            // put("record", true)          // optional; persist this session server-side
        }
        socketManager.socket.emit("start_session", payload)
    }
    
    fun stopSession() {
        if (!isSessionActive) return
        
        audioRecorder.stopRecording()
        socketManager.socket.emit("stop_session")
    }
    
    fun skipWord() {
        if (!isSessionActive) return
        socketManager.socket.emit("skip_word")
    }
    
    fun hasAudioPermission(): Boolean {
        return audioRecorder.hasPermission()
    }
}
```

### RecitationActivity.kt

```kotlin
package com.example.quran

import android.Manifest
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

class RecitationActivity : AppCompatActivity(), RecitationSessionListener {
    
    private lateinit var session: RecitationSession
    
    private lateinit var statusText: TextView
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private lateinit var skipButton: Button
    private lateinit var resultsRecyclerView: RecyclerView
    
    private val wordResults = mutableListOf<WordResult>()
    private lateinit var resultsAdapter: WordResultsAdapter
    
    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            startRecitationSession()
        } else {
            statusText.text = "Microphone permission required"
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_recitation)
        
        statusText = findViewById(R.id.statusText)
        startButton = findViewById(R.id.startButton)
        stopButton = findViewById(R.id.stopButton)
        skipButton = findViewById(R.id.skipButton)
        resultsRecyclerView = findViewById(R.id.resultsRecyclerView)
        
        resultsAdapter = WordResultsAdapter(wordResults)
        resultsRecyclerView.layoutManager = LinearLayoutManager(this)
        resultsRecyclerView.adapter = resultsAdapter
        
        session = RecitationSession(this)
        session.listener = this
        
        startButton.setOnClickListener {
            if (session.hasAudioPermission()) {
                startRecitationSession()
            } else {
                permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
        
        stopButton.setOnClickListener {
            session.stopSession()
        }
        
        skipButton.setOnClickListener {
            session.skipWord()
        }
        
        updateButtonStates(false)
    }
    
    override fun onStart() {
        super.onStart()
        session.connect()
    }
    
    override fun onStop() {
        super.onStop()
        session.disconnect()
    }
    
    private fun startRecitationSession() {
        wordResults.clear()
        resultsAdapter.notifyDataSetChanged()
        
        // Start session for Al-Fatiha (chapter 1, verses 1-7)
        session.startSession(startChapter = 1, startVerse = 1, endChapter = 1, endVerse = 7)
    }
    
    private fun updateButtonStates(isRecording: Boolean) {
        startButton.isEnabled = !isRecording
        stopButton.isEnabled = isRecording
        skipButton.isEnabled = isRecording
    }
    
    // RecitationSessionListener implementation
    
    override fun onSessionStarted() {
        runOnUiThread {
            statusText.text = "Recording..."
            updateButtonStates(true)
        }
    }
    
    override fun onWordResult(result: WordResult) {
        runOnUiThread {
            wordResults.add(result)
            resultsAdapter.notifyItemInserted(wordResults.size - 1)
            resultsRecyclerView.scrollToPosition(wordResults.size - 1)
        }
    }
    
    override fun onSessionStopped() {
        runOnUiThread {
            statusText.text = "Session complete"
            updateButtonStates(false)
        }
    }
    
    override fun onSessionError(reason: String) {
        runOnUiThread {
            statusText.text = "Error: $reason"
            updateButtonStates(false)
        }
    }
    
}
```

### WordResultsAdapter.kt

```kotlin
package com.example.quran

import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class WordResultsAdapter(
    private val results: List<WordResult>
) : RecyclerView.Adapter<WordResultsAdapter.ViewHolder>() {
    
    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val wordText: TextView = view.findViewById(R.id.wordText)
        val statusText: TextView = view.findViewById(R.id.statusText)
    }
    
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_word_result, parent, false)
        return ViewHolder(view)
    }
    
    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val result = results[position]
        
        holder.wordText.text = "Word ${result.wordNumber}"
        holder.statusText.text = result.status.name
        
        val color = when (result.status) {
            WordResult.Status.CORRECT -> Color.parseColor("#22C55E")
            WordResult.Status.INCORRECT -> Color.parseColor("#EF4444")
            WordResult.Status.SKIPPED -> Color.parseColor("#6B7280")
        }
        holder.statusText.setTextColor(color)
    }
    
    override fun getItemCount() = results.size
}
```

### Layout: activity_recitation.xml

```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:orientation="vertical"
    android:padding="16dp">

    <TextView
        android:id="@+id/statusText"
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:text="Ready"
        android:textSize="18sp"
        android:textAlignment="center"
        android:padding="16dp" />

    <LinearLayout
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:orientation="horizontal"
        android:gravity="center"
        android:padding="16dp">

        <Button
            android:id="@+id/startButton"
            android:layout_width="wrap_content"
            android:layout_height="wrap_content"
            android:text="Start"
            android:layout_marginEnd="8dp" />

        <Button
            android:id="@+id/stopButton"
            android:layout_width="wrap_content"
            android:layout_height="wrap_content"
            android:text="Stop"
            android:layout_marginEnd="8dp" />

        <Button
            android:id="@+id/skipButton"
            android:layout_width="wrap_content"
            android:layout_height="wrap_content"
            android:text="Skip" />
    </LinearLayout>

    <androidx.recyclerview.widget.RecyclerView
        android:id="@+id/resultsRecyclerView"
        android:layout_width="match_parent"
        android:layout_height="0dp"
        android:layout_weight="1" />

</LinearLayout>
```

### Layout: item_word_result.xml

```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:orientation="horizontal"
    android:padding="12dp">

    <TextView
        android:id="@+id/wordText"
        android:layout_width="0dp"
        android:layout_height="wrap_content"
        android:layout_weight="1"
        android:textSize="16sp" />

    <TextView
        android:id="@+id/statusText"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:textSize="16sp"
        android:textStyle="bold" />

</LinearLayout>
```

## Jetpack Compose Version

```kotlin
package com.example.quran

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

@Composable
fun RecitationScreen() {
    val context = LocalContext.current
    val session = remember { RecitationSession(context) }
    
    var statusText by remember { mutableStateOf("Ready") }
    var isRecording by remember { mutableStateOf(false) }
    val wordResults = remember { mutableStateListOf<WordResult>() }
    
    DisposableEffect(Unit) {
        session.listener = object : RecitationSessionListener {
            override fun onSessionStarted() {
                statusText = "Recording..."
                isRecording = true
            }
            
            override fun onWordResult(result: WordResult) {
                wordResults.add(result)
            }
            
            override fun onSessionStopped() {
                statusText = "Session complete"
                isRecording = false
            }
            
            override fun onSessionError(reason: String) {
                statusText = "Error: $reason"
                isRecording = false
            }
            
        }
        
        session.connect()
        
        onDispose {
            session.disconnect()
        }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text(
            text = statusText,
            style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        )
        
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center
        ) {
            Button(
                onClick = {
                    wordResults.clear()
                    session.startSession(1, 1, 1, 7)
                },
                enabled = !isRecording,
                modifier = Modifier.padding(4.dp)
            ) {
                Text("Start")
            }
            
            Button(
                onClick = { session.stopSession() },
                enabled = isRecording,
                modifier = Modifier.padding(4.dp)
            ) {
                Text("Stop")
            }
            
            Button(
                onClick = { session.skipWord() },
                enabled = isRecording,
                modifier = Modifier.padding(4.dp)
            ) {
                Text("Skip")
            }
        }
        
        Spacer(modifier = Modifier.height(16.dp))
        
        LazyColumn(
            modifier = Modifier.fillMaxWidth()
        ) {
            items(wordResults) { result ->
                WordResultItem(result)
            }
        }
    }
}

@Composable
fun WordResultItem(result: WordResult) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(12.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text("Word ${result.wordNumber}")
        
        Text(
            text = result.status.name,
            color = when (result.status) {
                WordResult.Status.CORRECT -> Color(0xFF22C55E)
                WordResult.Status.INCORRECT -> Color(0xFFEF4444)
                WordResult.Status.SKIPPED -> Color(0xFF6B7280)
            }
        )
    }
}
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Permission denied | Check runtime permission handling |
| AudioRecord init fails | Verify another app isn't using the mic |
| Connection timeout | Check network and server URL |
| No recognition results | Verify audio format (16kHz, mono, PCM16) |

### Debug Logging

```kotlin
// Enable Socket.IO logging
IO.setDefaultOkHttpCallFactory(
    OkHttpClient.Builder()
        .addInterceptor(HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        })
        .build()
)

// Log audio recording
println("Recording: $shortsRead shorts read")
```
