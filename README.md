# Middenaarde Overlay

A portable Windows companion with local translation and selectable offline or online narration for **The Lord of the Rings: Journeys in Middle-earth**. It recognizes English text in the game window, draws a Dutch translation over the same text area, and reads longer passages aloud in Dutch. It does not modify the game or its saves.

**Experimental release (0.2.4).** OCR, layout grouping and machine translation can make mistakes, especially with decorative fonts, icons, moving text and short rules terms. The first release has passed a synthetic OCR → translation → Dutch audio integration test and a Windows overlay-exclusion test. A complete live campaign has not been tested. Always check the English text when a rule or number seems odd.

**Use version 0.2.2 or newer.** The 0.2.0 portable package omitted an audio dependency; 0.2.1 fixed this and tests the packaged executable before publishing.

Version 0.2.2 also accepts case-insensitive Google PCM MIME types and decodes compressed audio returned by Google.

## Download and run — no Python required

1. On this repository's GitHub page, open **Releases** on the right and select the latest release.
2. Download **MiddenaardeOverlay-Windows-x64.zip** from **Assets**. **Code → Download ZIP** contains source code, not the ready-to-run app.
3. Right-click the downloaded ZIP and choose **Extract All**. Keep the entire extracted folder, including `_internal`. Do not run the EXE from inside the ZIP and do not copy the EXE by itself.
4. Open the extracted folder and double-click **MiddenaardeOverlay.exe**. No installation or administrator rights are required. The default Piper voice works without an account; online providers are optional.
5. Start the Steam version of **Journeys in Middle-earth** in **English**. Press **Alt+Enter** to use windowed mode. Exclusive fullscreen is unsupported.
6. In the companion app, wait for **Gereed**, click **Start overlay**, then click the game window.
7. Wait a few seconds on a stable text screen. Dutch text appears in dark, opaque panels over the detected English text. Mouse clicks pass through the panels to the game. Longer text is spoken automatically.

Use the same ZIP and steps on another Windows PC. Both the translation model and Dutch voice are included; after download, the app works offline. The app only targets the `JiME.exe` game window and pauses capture when the game loses focus. Its own overlay is excluded from capture to prevent translation loops. It does not save screenshots. OCR and translation stay local; selecting an online voice sends the Dutch narration text to that provider. Recognized text and translations are cached locally in `data/translations.json` next to the EXE.

### Requirements

- Windows 10 **version 2004 or newer**, or Windows 11; **64-bit x64**. ARM64 and 32-bit Windows are not tested.
- English Windows OCR support. It was available on the development PC. If startup reports missing English OCR, add **English (United States)** in **Settings → Time & language → Language & region → Language options**, including its OCR/text-recognition feature where offered. You do not need to change your Windows display language. Managed PCs may require their administrator to add this optional Windows feature.
- A CPU capable of running CTranslate2 and ONNX Runtime, and enough memory for the game plus two local models. GPU acceleration is not required. Performance on older hardware is unverified.
- Keep the extracted app in a folder where you can write, such as Downloads or Documents. It stores its cache and log in its own `data` subfolder.

The EXE is unsigned. If your Windows policy prevents it from running, use your normal trusted-software review process; do not disable Windows security protections. Source code and build instructions are provided below.

## Controls

**Klik op vertaling om voor te lezen / stoppen** (enabled by default): click a translated panel to read that block; click the same block again to stop loading or playback. Disable this option to pass all clicks through to the game. With it enabled, translated panels capture clicks; uncovered game areas remain clickable.

Version 0.2.3 adds **Vertaal vanaf** (minimum English block length, default 30 characters; 0 disables filtering) and a click-through status panel in the top-right of the game. A compact indicator reports recognition, translation, speech loading and errors; it disappears during playback and when ready. Settings are saved in `data/ui.ini`.


| Dutch control | What it does |
| --- | --- |
| **Start overlay / Pauzeer overlay** | Start or pause recognition and replacement text |
| **Verhaaltekst automatisch voorlezen** | Toggle automatic Dutch narration of paragraphs with at least 65 source characters |
| **Vertaal vanaf** | Skip English OCR blocks shorter than this number of characters (including spaces); default 30, set 0 for all text. Saved across restarts in `data/ui.ini`. Skipped blocks are neither covered nor narrated. |
| **Stop stem** | Stop the current speech |
| **Kies tekstgebied** | Drag over one English text area in the game; press Escape to cancel |
| **Hele spelvenster** | Reset to recognizing the entire game window |
| **Lees laatste tekst opnieuw** | Read the latest translation again, including short text when no story paragraph was found |
| **Ctrl+Alt+P** | Start/pause while the game is focused |
| **Ctrl+Alt+S** | Stop speech |

Each new text page is allowed to settle before translation. A translation is discarded if the source page changes while it is being translated. New text stops the old narration. Identical story text is not automatically narrated twice during the same app session; use the repeat button to hear it again.

The overlay uses OCR bounding boxes, not the game's internal text widgets. It covers the English with a plain dark background rather than recreating the original artwork. Font sizes shrink to fit, and very dense text can require a larger text area. Icons inside a recognized area may be covered. Use **Kies tekstgebied** to limit recognition to the story panel if whole-window detection is noisy. Moving or resizing the game triggers a new capture. Drag the companion controls away from the game area when possible.

### Troubleshooting

- **No overlay:** ensure the game is in English and windowed, click Start overlay, then bring the game to the foreground. The layer hides when you switch apps. Try selecting only the story panel. A black or empty game capture cannot be translated; this implementation uses Windows desktop capture, not game injection. Some graphics/capture configurations may need a different capture backend.
- **Speech stops when I click the companion:** this is expected when the game loses focus. Click **Lees laatste tekst opnieuw** to listen from the companion.
- **Hotkeys do nothing:** another app may already own them; use the buttons instead.
- **Text flickers or is missed:** wait on a stable screen, select a smaller text area, and avoid animations over the text. First-time translations take longer; cached ones are faster. There is no guaranteed real-time latency.
- **Wrong translation:** pause or close the overlay to see the English again. Add an exact English → Dutch entry to `glossary.json` and restart the app. Game-specific names and rules are not professionally translated.
- **App fails to start:** keep `_internal` beside the EXE. Check `data/startup-error.txt` or `data/overlay.log`. Logs are local; review their content before sharing them.
- **No audio:** check the Windows output device and volume. Check **Stem & providers**. Piper works offline; online voices require connectivity and, except Edge, an API key. A Windows Dutch voice is not needed.

## Nederlandse snelstart

1. Download onder **Releases → Assets** het bestand **MiddenaardeOverlay-Windows-x64.zip**.
2. Kies **Alles uitpakken**. Bewaar de hele uitgepakte map; `_internal` moet naast het programma blijven staan.
3. Dubbelklik op **MiddenaardeOverlay.exe**. Je hebt geen Python, account, API-sleutel of betaalde dienst nodig.
4. Open het spel in het **Engels** en zet het met **Alt+Enter** in venstermodus.
5. Wacht in de overlay op **Gereed**, klik op **Start overlay** en klik daarna in het spel.
6. De Nederlandse tekst verschijnt over de Engelse tekst. Langere verhaalteksten worden in het Nederlands voorgelezen. Dit kan enkele seconden duren.
7. Gebruik **Kies tekstgebied** als je alleen het verhaalvak wilt vervangen. Je kunt door de vertaling heen klikken om het spel te bedienen.

**Ctrl+Alt+P** pauzeert; **Ctrl+Alt+S** stopt de stem. Sluit de app om alle Engelse tekst weer normaal te zien. Vertaling en de standaard Piper-stem werken lokaal. Online stemmen versturen de Nederlandse tekst naar de gekozen dienst. Automatische vertaling en tekstherkenning kunnen fouten maken. Controleer belangrijke spelregels en getallen in de oorspronkelijke tekst.

## Natuurlijkere stemmen / Natural voices

Open **Stem & providers…**, select a provider and voice, and click **Luistervoorbeeld**. Adjust tempo and volume, then **Save**. The default remains Piper, so updating never starts sending your game text online without your choice. Enable **Verhaaltekst automatisch voorlezen** if a previous speech error disabled it.

| Provider | Setup | Voice controls |
| --- | --- | --- |
| Piper | Included, offline, no account | 52 speaker IDs, tempo, volume |
| Microsoft Edge | Online, no API key; unofficial edge-tts service | Dutch and Flemish neural voices, tempo, volume |
| Google AI / Gemini | Create a key in [Google AI Studio](https://aistudio.google.com/apikey) | Voice, editable TTS model, natural-language storytelling style, requested tempo, volume |
| ElevenLabs | Create a key in [ElevenLabs](https://elevenlabs.io/app/settings/api-keys); allow text-to-speech and voice listing | Fetch account voices or enter Voice ID, model, tempo, stability, similarity, expressiveness, volume |
| Azure Speech | Create a Speech resource in Azure; enter its key and region | Fetch Dutch/Flemish voices, tempo, volume |

**Nederlands:** Kies bijvoorbeeld **Google AI · Gemini**, vul je API-sleutel in en kies **Charon** of **Orus**. Beschrijf bij **Google-vertelstijl** bijvoorbeeld: “Vertel in natuurlijk Nederlands als een warme, spannende fantasyverteller. Neem rust bij dramatische momenten.” Klik op **Luistervoorbeeld**, daarna **Save**. Bij ElevenLabs haal je eerst je stemmen op; meer expressiviteit en iets minder stabiliteit kunnen de vertelling levendiger maken. De uitkomst verschilt per stem en tekst. Edge is de optie zonder API-sleutel; het is een onofficiële koppeling en kan buiten werking raken.

Google's default is `gemini-3.1-flash-tts-preview`; the model field is editable because preview models can change. Its tempo is a prompt instruction, not a guaranteed playback rate. Google style instructions apply only to Gemini. ElevenLabs expressiveness maps to its numeric style setting. Free allowances, model access and API voice access depend on the provider and your account; an API key does not mean unlimited free usage. Check [Google pricing](https://ai.google.dev/gemini-api/docs/pricing), [ElevenLabs pricing](https://elevenlabs.io/pricing) and [Azure pricing](https://azure.microsoft.com/pricing/details/cognitive-services/speech-services/). The app never switches to another paid provider automatically.

### Keys, privacy and upgrades

API keys are masked in the dialog and saved using **Windows DPAPI for the current Windows user** at `%LOCALAPPDATA%\MiddenaardeOverlay\credentials.dpapi`. They are not written to source files, ordinary settings, logs or GitHub. This protects stored keys; software running as your Windows account can still access them. Clearing a key field and saving removes that saved key. Enter keys once again on another PC; do not copy the encrypted credentials file between machines.

Voice preferences are saved in `data/voices.json` next to the EXE. On upgrade, close the old app, extract the new ZIP to a new folder and copy `data/voices.json` and optionally `data/translations.json` into its `data` folder. Keys are found automatically through your Windows user profile. Do not mix old and new `_internal` folders.

The app caches recent synthesized audio in memory (up to 40 MB), so repeating the same text with identical settings during a session normally avoids another request. Playback uses two local WAV files in `data/`; these contain recent spoken text. Stop silences playback immediately, but cannot refund/cancel an online request already sent. Failed requests are not automatically retried. On a provider error, correct settings and use the repeat button or enable automatic narration again.

Provider payloads, error handling, cancellation and real Windows DPAPI round trips are covered by automated tests. Google, ElevenLabs and Azure require your own credentials for an end-to-end listening test; their live APIs have not been tested with a customer key.

## Run from source

Requires **Python 3.12 x64** with pip, on Windows. Run these commands from a terminal in this source folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe download_models.py
.venv\Scripts\python.exe src\launcher.py
```

Setup downloads approximately 71 MB of translation weights and 63 MB of voice weights, plus Python dependencies. The download script verifies SHA-256 hashes. Downloads happen during setup/build only. Optional online speech providers connect only when you request narration or fetch voices.

To run the integration check without starting the game:

```powershell
.venv\Scripts\python.exe src\selftest.py self-test.json
```

This generates a synthetic English text image, runs real Windows OCR and local translation, synthesizes Dutch speech into `self-test.wav`, and checks capture exclusion for a test window. It opens a small temporary test window but does not advance your game or play audio. A successful JSON result contains `"result": "PASS"`.

## Build the standalone app

Double-click **BUILD.cmd**, or after the source setup run:

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm MiddenaardeOverlay.spec
```

The app is built into `dist/MiddenaardeOverlay/`. Copy `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md` and `glossary.json` into that folder before distributing it. Distribute the **whole folder as a ZIP**, never just the EXE.

The included **Build Windows app** GitHub Actions workflow builds on Windows and provides a downloadable artifact. It checks Python sources and attempts the native integration check; that native check is non-blocking because hosted runners may lack Windows OCR or a composed desktop. Run the local self-test on your own PC to check those features. Run the workflow under **Actions → Build Windows app → Run workflow**. Pushing a `v*` tag also creates a GitHub Release with the ready-to-run ZIP. The workflow needs repository contents-write permission to publish releases; it does not use any personal access token.

Pushes to `main` also build a downloadable artifact. For publication, a `main` commit whose message starts with `[release]` publishes version `v0.2.2` automatically. For future versions, update the app version and workflow fallback tag, or publish a matching new `v*` tag.

## Publish this source as your own public GitHub repository

If this source was provided before a repository could be created:

1. Sign in to GitHub yourself and create a **public** repository named `middenaarde-overlay`.
2. Upload this folder's source files, including `src/`, `.github/workflows/`, the build scripts, README, requirements, notices and license. Do **not** upload `lib/`, `model/`, `voice/`, `data/`, generated WAVs or local logs. These are excluded by `.gitignore` for Git users.
3. On GitHub, open **Actions**, enable workflows if GitHub asks, and run **Build Windows app**. The downloaded workflow artifact contains the app ZIP.
4. To offer a simple public download under **Releases**, create/push the tag `v0.2.2` (GitHub's **Code → Tags → Create new release** can also create a tag). The tag workflow will build and attach the app ZIP to that release.

For a Git/CLI upload, use your normal Git identity and authenticated GitHub CLI:

```powershell
git init
git add .gitignore .github src requirements.txt download_models.py MiddenaardeOverlay.spec BUILD.cmd README.md THIRD_PARTY_NOTICES.md LICENSE glossary.json
git commit -m "Add offline Dutch game overlay"
gh repo create middenaarde-overlay --public --source . --remote origin --push
git tag v0.2.2
git push origin v0.2.2
```

## How it works and licensing

Windows OCR → paragraph grouping and stable-page check → local CTranslate2 / OPUS-MT English–Dutch model → transparent, click-through Qt window → selected Piper, Microsoft Edge, Google Gemini, ElevenLabs or Azure narration. A small exact-match glossary and disk cache reduce repeat work. The overlay is a separate app; it is not a game mod and does not bundle the game's copyrighted text, artwork or save files.

App source: **GPL-3.0-or-later**. Models and dependencies retain their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), including attribution to the OPUS-MT authors and Piper/MLS voice dataset. No affiliation with Fantasy Flight Games, Middle-earth Enterprises, Google or Steam.
