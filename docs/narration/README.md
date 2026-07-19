# ElevenLabs narration production brief

## Goal

Create a warm, credible portfolio narration that a recruiter or nontechnical
team member can follow without watching every detail on screen. The finished
read should feel like a thoughtful product walkthrough, not a commercial or a
movie trailer.

The copy-ready source is [`elevenlabs-script.txt`](elevenlabs-script.txt). Paste
only that file's contents into ElevenLabs; the notes below are production
guidance and should not be spoken.

## Delivery direction

- **Tone:** calm, assured, curious, and conversational.
- **Pace:** about 0.96 to 1.0 speed, targeting 72–86 seconds.
- **Energy:** engaged but restrained; avoid exaggerated enthusiasm.
- **Pauses:** keep the paragraph breaks. Give the three study results enough
  space to remain distinct.
- **Emphasis:** lightly emphasize “safe,” “all thirty,” “spent nothing,” and
  the final contrast between variable agents and dependable guardrails.
- **Pronunciation:** “Aurora” is *uh-ROAR-uh*. “A. I.” should be spoken as two
  letters. “Telemetry” does not appear in this version to avoid unnecessary
  jargon.

## Suggested starting settings

For a stable recruiter-facing read, start with Eleven Multilingual v2 and a
neutral North American English voice. Voice choice matters more than fine
slider adjustments.

- Stability: 50
- Similarity: 75
- Style exaggeration: 0
- Speed: 0.96–1.0
- Speaker Boost: on, if available for the selected model and voice

These are starting points, not hard requirements. If the selected voice sounds
too flat, lower stability slightly before adding style exaggeration. If it
sounds rushed, reduce speed or preserve a longer pause between paragraphs.
This baseline follows ElevenLabs' current
[Text to Speech product guide](https://elevenlabs.io/docs/eleven-creative/playground/text-to-speech),
which recommends starting near 50 stability, 75 similarity, and zero style
exaggeration.

## Export handoff

1. Generate the complete script as one read so the tone stays consistent.
2. Listen for the pronunciation of “Aurora,” “A. I.,” and the three study
   results.
3. If possible, download a WAV master. An MP3 is also acceptable; ElevenLabs
   currently offers both from Text to Speech history, and the site copy can be
   optimized after delivery.
4. Name the downloaded file `aurora-elevenlabs-master.wav` or
   `aurora-elevenlabs-master.mp3`.
5. Attach that file to the Codex task.

After the audio is attached, the integration pass will:

1. inspect the recording and exact duration;
2. create an optimized browser audio file while retaining the master;
3. replace the temporary synthetic narration;
4. update the transcript, captions, chapter markers, and displayed duration;
5. verify playback and accessibility; and
6. redeploy the public Fischer Product Lab demo.

## Planned chapters

The exact timings will be set from the finished recording.

1. What Aurora tests
2. How the control plane works
3. Three controlled results
4. How to explore the evidence
5. Honest limits and the model-backed seam

ElevenLabs' current download instructions are available in its
[official help article](https://help.elevenlabs.io/hc/en-us/articles/14129286847505-How-do-I-download-generated-files-from-Text-to-Speech).
