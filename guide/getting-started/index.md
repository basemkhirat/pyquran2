# Getting Started

This guide covers how to connect to the Quran Socket.IO server and authenticate your client.

## Prerequisites

Before integrating, ensure you have:

- **Socket.IO client library** installed for your platform
- **Server URL**: `https://websocket.zekr.online`
- **API key** (authentication is enabled by default)

## Client Libraries

| Platform | Library | Installation |
|----------|---------|--------------|
| iOS | socket.io-client-swift | `pod 'Socket.IO-Client-Swift'` |
| Android | socket.io-client-java | `implementation 'io.socket:socket.io-client:2.1.0'` |
| Flutter | socket_io_client | `flutter pub add socket_io_client` |

## Server Configuration

| Setting | Value |
|---------|-------|
| Protocol | Socket.IO (not raw WebSocket) |
| Transport | `websocket` (required) |
| Path | `/socket.io` (default) |
| Base URL | `https://websocket.zekr.online` |

## Connection Setup

The server uses Socket.IO, which provides automatic reconnection, event-based communication, and binary support. Always use the `websocket` transport and include the API key (authentication is required).

::: code-group

```javascript [JavaScript]
import { io } from "socket.io-client";

const socket = io("https://websocket.zekr.online", {
  autoConnect: false,
  transports: ["websocket"],
  auth: { api_key: "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY" }
});

socket.connect();
```

```swift [Swift]
import SocketIO

let manager = SocketManager(
    socketURL: URL(string: "https://websocket.zekr.online")!,
    config: [
        .log(true),
        .compress,
        .forceWebsockets(true),
        .connectParams(["api_key": "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"])
    ]
)

let socket = manager.defaultSocket
socket.connect()
```

```kotlin [Kotlin]
import io.socket.client.IO
import io.socket.client.Socket

val options = IO.Options().apply {
    transports = arrayOf("websocket")
    auth = mapOf("api_key" to "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY")
}

val socket: Socket = IO.socket("https://websocket.zekr.online", options)
socket.connect()
```

```dart [Dart]
import 'package:socket_io_client/socket_io_client.dart' as IO;

IO.Socket socket = IO.io('https://websocket.zekr.online', 
  IO.OptionBuilder()
    .setTransports(['websocket'])
    .setAuth({'api_key': 'mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY'})
    .disableAutoConnect()
    .build()
);

socket.connect();
```

:::

## Authentication

Authentication is **enabled by default**. Clients must provide a valid API key during the connection handshake.

### How It Works

1. Client includes the API key in the `auth` option during connection
2. Server validates the key before accepting the connection
3. Invalid or missing keys result in connection rejection

### Default API Key

Use this API key in your connection (required):

```
mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY
```

### Connection with Auth

::: code-group

```javascript [JavaScript]
import { io } from "socket.io-client";

const socket = io("https://websocket.zekr.online", {
  transports: ["websocket"],
  auth: {
    api_key: "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"
  }
});
```

```swift [Swift]
import SocketIO

let manager = SocketManager(
    socketURL: URL(string: "https://websocket.zekr.online")!,
    config: [
        .forceWebsockets(true),
        .connectParams(["api_key": "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"])
    ]
)

// Or using auth in the connect call
socket.connect(withPayload: ["api_key": "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY"])
```

```kotlin [Kotlin]
import io.socket.client.IO
import org.json.JSONObject

val options = IO.Options().apply {
    transports = arrayOf("websocket")
    auth = mapOf("api_key" to "mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY")
}

val socket = IO.socket("https://websocket.zekr.online", options)
```

```dart [Dart]
import 'package:socket_io_client/socket_io_client.dart' as IO;

IO.Socket socket = IO.io('https://websocket.zekr.online',
  IO.OptionBuilder()
    .setTransports(['websocket'])
    .setAuth({'api_key': 'mWrhEBxIUstdokyV5FynGaqN0zowUYaCFvW88RdzYeY'})
    .build()
);
```

:::

## Connection Lifecycle

Handle connection events to manage the socket state:

::: code-group

```javascript [JavaScript]
socket.on("connect", () => {
  console.log("Connected to server");
});

socket.on("disconnect", (reason) => {
  console.log("Disconnected:", reason);
});

socket.on("connect_error", (error) => {
  if (error.message === "authentication_failed") {
    console.error("Invalid API key");
  } else {
    console.error("Connection failed:", error.message);
  }
});
```

```swift [Swift]
socket.on(clientEvent: .connect) { data, ack in
    print("Connected to server")
}

socket.on(clientEvent: .disconnect) { data, ack in
    print("Disconnected")
}

socket.on(clientEvent: .error) { data, ack in
    if let error = data.first as? String,
       error.contains("authentication_failed") {
        print("Invalid API key")
    }
}
```

```kotlin [Kotlin]
socket.on(Socket.EVENT_CONNECT) {
    println("Connected to server")
}

socket.on(Socket.EVENT_DISCONNECT) {
    println("Disconnected")
}

socket.on(Socket.EVENT_CONNECT_ERROR) { args ->
    val error = args[0] as? Exception
    if (error?.message?.contains("authentication_failed") == true) {
        println("Invalid API key")
    } else {
        println("Connection failed: ${error?.message}")
    }
}
```

```dart [Dart]
socket.onConnect((_) {
  print('Connected to server');
});

socket.onDisconnect((_) {
  print('Disconnected');
});

socket.onConnectError((error) {
  if (error.toString().contains('authentication_failed')) {
    print('Invalid API key');
  } else {
    print('Connection failed: $error');
  }
});
```

:::