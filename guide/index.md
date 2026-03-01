---
layout: home

hero:
  text: Real-time Quran Voice Recognition API
  tagline: The Integration guide
  actions:
    - theme: brand
      text: Get Started
      link: /getting-started/
    - theme: alt
      text: View Events
      link: /events/
---

## Introduction

It is a Quran voice recognition project that:

- Accepts real-time audio streaming from mobile devices
- Transcribes Arabic Quran recitation using Whisper and Wav2vec2.
- Scores each word for correctness (correct, incorrect, or skipped)
- Returns results word-by-word as the user recites

## Requirements

- Socket.IO client library for your platform
- Microphone access for audio capture
- Network connectivity to the API server
