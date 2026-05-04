# Speech Future Plan

Speech is not part of the current MVP. This document only records the planned direction.

## Russian ASR

Primary candidate: `GigaAM-v3` from Salute/Sber.

Reasons:

- Open weights and MIT license.
- Strong Russian ASR focus.
- Conformer-based model around 220-240M parameters.
- Variants include `v3_ctc`, `v3_rnnt`, `v3_e2e_ctc`, and `v3_e2e_rnnt`.
- End-to-end variants provide punctuation and text normalization.

Recommended first test later:

- `v3_e2e_rnnt` for interactive transcription.
- `v3_e2e_ctc` if simpler batching is more stable.

## ASR Fallback

Fallback candidate: Whisper large-v3-turbo or faster-whisper.

Use it when:

- Multilingual recognition matters more than Russian quality.
- Tooling maturity matters more than best Russian WER.
- GigaAM integration is blocked.

## Speech-To-Speech

Candidate to investigate later: Kyutai Moshi.

Moshi is interesting because:

- It is designed for full-duplex spoken dialogue.
- It has MLX and Rust/Candle support.
- It is built around a streaming neural audio codec.

Main concern:

- Russian support is not a reliable assumption.

## Recommended Russian Voice Architecture

For Russian voice, start with a cascade:

```mermaid
flowchart LR
    Mic[Microphone] --> VAD[VAD]
    VAD --> ASR[GigaAM_v3]
    ASR --> Agent[Local_Agent]
    Agent --> TTS[TTS]
    TTS --> Speaker[Speaker]
```

This is less elegant than true speech-to-speech, but it is more controllable, easier to debug, and more likely to work well in Russian.

## Explicit Non-Goals For Current MVP

- Do not implement ASR now.
- Do not implement TTS now.
- Do not integrate Moshi now.
- Do not optimize audio streaming now.
