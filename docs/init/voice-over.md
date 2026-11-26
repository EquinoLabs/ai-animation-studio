Since you've completed the script-generation part, AI voiceover should be your next pipeline stage before you start generating visuals.

For your faceless YouTube automation project, I'd avoid relying on paid APIs initially. You have an RTX 4050 6GB and are already running Ollama locally, so you have a few good options for generating narration without paying per video.

## **1. Best voice generation options**

### **Option 1 — Kokoro TTS (my first recommendation)**

Local • Free • Lightweight • Good quality

[GitHub - hexgrad/kokoro: https://hf.co/hexgrad/Kokoro-82M · GitHub](https://images.openai.com/static-rsc-4/docbzTgB6X6jSlhPaeNH-4zT7SixbegbJOY_Msfiqec4WsicLASUzmHAxeLPkUDZfo1KwpLLvUPx2-5ZJjre0aHotTuzW1FqMLTo29L6lS34xASgOgxPsPgiZG7Z3ySY9rzcJUeWkSg_YfSBCG-FW3a8317kkNqe5pPRszhYL20?purpose=inline)

[What Is Kokoro-82M? Why It Fits Browser Text to Speech | Resodock](https://images.openai.com/static-rsc-4/x6v-mNgYM_JOVt2AY-dAtkygrT3eMAavXg7uwJgQ50RkrZLoelWOH_LnKxyYVFMhbqEkuXldcFEzlWYmvGHTgVjj1DUgWvrmMpQJj9Ng2myOXHsIgfEY8ieLcRrz3NUTzaehjpzTZSjEoTPlLieIRWkaa7BIEnLu68K1eMzOrIU?purpose=inline)

[82Mの軽量TTS「Kokoro」をローカルPC(CPU)で動かしてみた｜あさぎ　あい](https://images.openai.com/static-rsc-4/HoqaTeXgtD35Fyt56AG_ka28LMJQQ4DDL_Myxi512ETP_X-WIerlJ5KRQWYRcBRsvWZ5SOnElk59nn3MU9ZqlNDoYOJwsYFRXXDZyp5KXSwicCWDSqx0ZyXc5yoAn9H7Qvbzv47M7oLlg6QeK4_xGDV182iYuuGF321QN1hEt8E?purpose=inline)

7

Kokoro TTS is probably the first model I'd test for your use case.

- Lightweight model (82M parameters).
- Natural-sounding voices for English.
- Runs locally.
- Very low hardware requirements compared with larger voice models.
- Good for long-form narration.
- Supports multiple voices and languages.
- Can be integrated into a Python pipeline.

Why I'd choose it: You want to produce potentially dozens of videos. The cost of generating audio shouldn't scale with every video, and Kokoro gives you a good starting point for that.

### **How to use Kokoro**

- Project: [Kokoro GitHub](https://github.com/hexgrad/kokoro?utm_source=chatgpt.com)
- Model: [Kokoro-82M on Hugging Face](https://huggingface.co/hexgrad/Kokoro-82M?utm_source=chatgpt.com)

I would test a few voices before settling on one. For faceless videos, the voice itself is part of your channel identity.

### **Option 2 — Piper TTS**

Local • Free • Fast • Very lightweight

[piper1-gpl/docs/API_PYTHON.md at main · OHF-Voice/piper1-gpl · GitHub](https://images.openai.com/static-rsc-4/qGlaWPzvSMxVHOjz8HtRFUyI8ONoTx5dr9eW7hiUUjS2W5DoUpRMIki8i7r_qkZAUJNpCrDwkJTz2ivMxbcvuDPeC7exYRm9pYQNSk8NKvlXy4uVG2Dh53ap90sO0GTFZSEDmEbUH976-dPP27wQNlR8OgUPYk9-YG-TylKE1t8?purpose=inline)

[Piper TTS Reviews in 2026](https://images.openai.com/static-rsc-4/dsDc1u5gyKhks5_ZxlHbWMiJHJr73zefzL7OaDaP9QPSsnu_J7cul-wuhJaEZ6ubFneeNtd_faj5o75ahMs8fOuHOjNaWcPOvYDcmnr57T8VjKtRdDRX5ZaZxGOIp5ryWKyhLndFC2YqK7i67NPTxqcrKk_ZBWDCo-dREuGEe1E?purpose=inline)

[app.py · dev-khadimali/piper-tts at main](https://images.openai.com/static-rsc-4/r2lOAUzJhwFXBAGZKQpC1I6mUQTREBp2wR8eb6eRwELq1HQGYebLiSW6om8qY7ZXNyO5K-FOEoYVIGrh_i1pqkOHuiZjjVPL5sMPe57uN9ubi7XHzSH94GqRsCQKecK2pDuCIEGStx64z0zo8QVwkCssyhB5CjBCRJsPrwMTh8w?purpose=inline)

5

Piper TTS is a very practical option if you prioritize speed, simplicity and low resource usage.

- Runs locally.
- Fast generation.
- Can run on CPU.
- Easy Python integration.
- Lots of voice models.

Trade-off: Some voices sound less expressive or polished than higher-quality neural TTS models.

\- [Piper repository and usage](https://github.com/k2jama/piperTTS?utm_source=chatgpt.com)

### **Option 3 — XTTS-v2**

Local • Voice cloning • Multilingual

[Coqui XTTS V2 - a Hugging Face Space by ohmykush](https://images.openai.com/static-rsc-4/DwjDaBcdEJWnGorFtpmGc1CHhPigcV40fCTWaXIrXvRxyYwTjhyoaGiMIyHITS2bBbvhGBBOFRS_njTejo7PCfrP8l5uzMTvfxI_yNr3K42u5ogvYimO3UDg45_3400JSSY3SyPxfNGkuycesYcBdPW-6c_M0gd0xlsDUThaaSg?purpose=inline)

[South Indian Languages Voice Cloning | by Madhumitha Reddy | Medium](https://images.openai.com/static-rsc-4/73wYNjwam-MJHCvRwMbrN39MyHFEOBl_YLyvVXDB8NQ_MeLGyL9V5bFUzyL-I-3OdM_0BnYoHwmbgQw5iPOaXwjPAkHpiHiiCYwmQB-b2-oXzR69Bri4l713S2CiCiGgNSq1UFt6Jd3_A0uyRiAYe1GPSXVFMmjyO8sqSuPvo8M?purpose=inline)

[Revolutionizing Video Editing: Harnessing the Power of Voice Cloning | by Jamie W. | Medium](https://images.openai.com/static-rsc-4/11s5HieZiLja358dpfnK8mHUiyfvhk29_KR7O2HekajyrV3G9qZpchxuR6Dsq_L4mQN9iDYUu9jb9rx6R0KVJUaNgV4NdF78_iJSGTjGVlDPUAEC1waP63OidLJXljV9JDlqyOvOoA9jAKl0rzH3O7qdAj7651NyNkuuq4NOTxs?purpose=inline)

6

XTTS-v2 is interesting if you want to create a recognizable narrator voice by using an audio reference.

- Voice cloning from a short reference recording.
- Multilingual.
- Python integration.
- Can generate WAV output.

However, be careful with its model license: XTTS-v2's Coqui Public Model License restricts use, including commercial use. I would not build a monetized YouTube business around it without resolving that licensing issue.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.coqui.ai\&sz=32)

TTS 0.22.0 documentation+1



## **2. Free online APIs**

If you don't want to install models or want to compare voice quality, these are worth considering.

| Provider               | My opinion                       | Downside                                                 |
| ---------------------- | -------------------------------- | -------------------------------------------------------- |
| ElevenLabs             | Excellent quality                | Free tier is limited                                     |
| Google Cloud TTS       | Good scalable option             | Billing setup and quotas                                 |
| Microsoft Azure Speech | Good voices and API              | Account and quota restrictions                           |
| Edge TTS               | Easy and free to experiment with | Unofficial service, not a reliable production dependency |

- [ElevenLabs](https://elevenlabs.io/?utm_source=chatgpt.com)
- [Google Cloud TTS](https://cloud.google.com/text-to-speech?utm_source=chatgpt.com)
- [Azure TTS](https://azure.microsoft.com/en-us/products/ai-services/text-to-speech?utm_source=chatgpt.com)
- [Edge TTS Python library](https://github.com/rany2/edge-tts?utm_source=chatgpt.com)

Google currently advertises monthly free character allowances for some TTS voice tiers, but quotas and billing requirements vary by voice type.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://cloud.google.com\&sz=32)

Google Cloud+1



My advice: Use local Kokoro first. If it doesn't sound good enough for your channel, compare the same script with ElevenLabs. Don't build your entire pipeline around a paid API before you know what quality you need.

## **3. What I would build next in your Python project**

Don't just convert the entire script into one MP3. Build a voiceover module.

Your pipeline should now look like this:

1\. Script Generator (Ollama)

Completed

2\. Script Cleaner

Remove unwanted formatting, normalize numbers, handle abbreviations

3\. TTS Voice Engine

Kokoro local model

4\. Audio Chunking & Assembly

Generate, retry, join and add pauses

5\. Audio Quality Check

Duration, silence, clipping and playback verification

6\. Final Narration.wav

Ready for video scene planning

### **Step 1 — Structure your project**

I'd keep the TTS engine isolated from the rest of your application.

```
youtube_automation/
│
├── script/
│   ├── script_generator.py
│   └── script_cleaner.py
│
├── audio/
│   ├── tts_engine.py
│   ├── audio_processor.py
│   └── audio_quality.py
│
├── visual/
│   ├── scene_planner.py
│   ├── image_generator.py
│   └── stock_fetcher.py
│
├── renderer/
│   └── video_renderer.py
│
├── pipeline.py
│
├── projects/
│   └── nokia_documentary/
│       ├── script.txt
│       ├── audio/
│       ├── visuals/
│       ├── scenes.json
│       └── final.mp4
│
└── config.py
```

### **Step 2 — Generate the narration**

The script cleaner takes the final script and prepares it for TTS.

For example, the original script:

> In 2007, Apple changed the mobile phone industry. Its first iPhone introduced an entirely different way of interacting with a phone.

Your cleaner should handle:

- Numbers and dates.
- Abbreviations.
- Unusual words and company names.
- Paragraph boundaries.
- Punctuation for natural pauses.

Then your TTS engine generates the audio.

### **Step 3 — Generate audio in chunks**

Don't send a 15-minute script in one request to the model.

Split it into paragraph-sized or sentence-group chunks, for example:

| Chunk | Text                      | Output    |
| ----- | ------------------------- | --------- |
| 001   | Opening narration         | `001.wav` |
| 002   | Context about the company | `002.wav` |
| 003   | Its rise to dominance     | `003.wav` |
| 004   | The turning point         | `004.wav` |
| 005   | The decline               | `005.wav` |

Why?

- If a generation fails, you regenerate only that chunk.
- You can correct pronunciation without regenerating everything.
- You can add pauses between paragraphs.
- You can track which part of your script corresponds to which audio.
- You can eventually use individual chunk durations for scene planning.

Your Python pipeline should automatically concatenate these chunks into a single narration file.

### **Step 4 — Add audio metadata**

Your pipeline should save something like:

```
{
  "audio_file": "narration.wav",
  "voice": "kokoro_voice_id",
  "sample_rate": 24000,
  "duration_seconds": 486.32,
  "chunks": 42,
  "status": "completed"
}
```

This information will help the next stage.

## **4. What comes after voiceover? (This is the important part)**

Audio → Scene planning → Visual generation → Rendering

Once your voiceover is generated, don't immediately start generating images.

The next stage should be an **audio-aware scene planner**.

For example:

| Timestamp   | Narration                                           | Visual                     |
| ----------- | --------------------------------------------------- | -------------------------- |
| 00:00–00:07 | Nokia dominated the mobile phone industry           | Historical phone imagery   |
| 00:07–00:13 | Millions of people relied on its devices            | Crowd / mobile phones      |
| 00:13–00:19 | But a new competitor was about to change everything | Dramatic smartphone reveal |
| 00:19–00:25 | Apple introduced the iPhone                         | Product-focused visual     |
| 00:25–00:31 | The industry began changing rapidly                 | Animated market chart      |

These are illustrative timings. In your actual pipeline, the scene planner should work from measured narration timings.

The ideal workflow is:

**Script**

Your final written narration

**Voiceover**

Generate and validate the narration audio

**Audio alignment**

Determine when each sentence is spoken

**Scene planner (LLM)**

Decide what visual is needed for each narration segment

**Visual assets**

Images, stock video, charts, maps, text graphics

**Renderer**

FFmpeg + Python, transitions, captions and music

Final YouTube Video

For audio alignment, I'd consider Whisper-based transcription/alignment tools or a forced-alignment tool to map your script to the actual audio. This will help you make scene changes at natural narration boundaries rather than guessing.

## **5. What I'd do right now**

Phase 1: Get the voiceover pipeline working before tackling visuals.

1. Install Kokoro.
2. Test 3–5 voices against the same sample script.
3. Select a consistent narrator voice.
4. Build a Python TTS wrapper.
5. Generate audio chunk by chunk.
6. Automatically merge chunks.
7. Save the final narration and metadata.
8. Verify the audio by listening and running basic audio checks.

Then start your scene planner.

I can help build this next as a proper **Python implementation of your AI YouTube pipeline, starting with Kokoro TTS installation, voice selection, chunk generation, and automatic audio assembly**, so you can test it on your actual script before moving to visual generation.

-----

Yes — **you can absolutely use Kokoro directly inside your own Python project. You do not need to install the MSI-based desktop/application workflow and then manually use it.**

The MSI/downloader you're seeing is likely for a particular packaged implementation or setup route. The underlying Kokoro model has a Python library interface. The official model ecosystem supports direct Python inference, including downloading model assets as part of its setup.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+1



## **Two approaches**

| Approach                      | How it works                                              | My recommendation                |
| ----------------------------- | --------------------------------------------------------- | -------------------------------- |
| Python library directly       | Your project imports Kokoro, loads model, generates audio | Best for your current prototype  |
| Local TTS server + Python API | Kokoro runs separately; your project sends HTTP requests  | Best for a more modular pipeline |

## **1. Directly use Kokoro in your Python project**

This is the route I would take right now.

You can install the Python package, download the model/voice assets when first needed, and generate WAV files directly from your script.

### **Step A — Create a dedicated environment**

On Windows, I'd use Python 3.11 for this initial setup.

```
mkdir tts_test
cd tts_test

py -3.11 -m venv .venv
.\.venv\Scripts\activate

python -m pip install --upgrade pip
pip install "kokoro>=0.9.4" soundfile
pip install "misaki[en]"
```

### **Step B — Install eSpeak NG**

This is the part where the MSI is useful.

Kokoro uses phonemization support for pronunciation processing. On Windows, installing eSpeak NG can help satisfy this dependency.

- Download the Windows installer from the [eSpeak NG releases page](https://github.com/espeak-ng/espeak-ng/releases?utm_source=chatgpt.com).
- Install it.
- Restart your terminal.

The MSI is for the phonemization dependency, not a separate TTS application you need to run manually.

### **Step C — Create a test file**

Create `test_tts.py`:

```
from kokoro import KPipelineimport soundfile as sfpipeline = KPipeline(lang_code="a")text = """The world of technology is changing rapidly.But not every company is prepared for what comes next."""generator = pipeline(    text,    voice="af_heart",)for i, (gs, ps, audio) in enumerate(generator):    sf.write(f"output_{i}.wav", audio, 24000)print("Audio generated!")
```

Run:

```
python test_tts.py
```

This is a basic test example. Depending on the installed Kokoro version and voice configuration, generation may yield one or multiple audio segments.

The model and associated voice assets are typically downloaded or cached as needed. Once they're locally available, you can use them without manually launching a downloader every time.

The official Python inference example uses `KPipeline`, and the model is available through the Python package ecosystem.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+1



## **2. Download the model from Hugging Face instead?**

You can, but I wouldn't manually download and manage individual model files unless you need a custom deployment or offline packaging.

My preferred approach:

- Install the Python package.
- Let the library handle model loading and asset downloads.
- Keep the Hugging Face cache.
- Don't copy model files into your source-code repository.
- Store generated audio inside your individual video project folders.

That keeps your code clean:

```
youtube_automation/
├── src/
│   ├── script_generator.py
│   ├── tts_engine.py
│   └── pipeline.py
│
├── cache/             # Optional: your own cache/config
│
├── projects/
│   ├── video_001/
│   │   ├── script.txt
│   │   └── narration.wav
│   └── video_002/
│
└── requirements.txt
```

My recommendation: use the direct Python approach first, rather than the MSI downloader application.

It will integrate naturally into your existing automation pipeline, and you can later switch the implementation to an HTTP-based TTS service without changing your script generator.

If you're comfortable with this, the next thing I'd build is a reusable `tts_engine.py` that handles long scripts, chunking, voice configuration, retries, WAV assembly and audio metadata — not just a one-off script.
