# Third-party notices

This companion does not contain Journeys in Middle-earth text, art, sounds or other game assets.

## Translation model

- Model: Argos Translate English → Dutch, package `translate-en_nl-1_8`.
- Source: https://data.argosopentech.com/argospm/v1/translate-en_nl-1_8.argosmodel
- Argos project: https://github.com/argosopentech/argos-translate
- Original OPUS-MT model authors: **Jörg Tiedemann and Santhosh Thottingal**.
- Reference: *OPUS-MT — Building open translation services for the World*, Proceedings of the 22nd Annual Conference of the European Association for Machine Translation (EAMT), 2020, Lisbon, Portugal.
- Model license, as stated in the package README: **Creative Commons Attribution 4.0 International (CC BY 4.0)**, https://creativecommons.org/licenses/by/4.0/
- Weights are redistributed unchanged. The app uses a local CTranslate2 inference wrapper and exact-match glossary.

## Dutch voice

- Model: **Piper `nl_NL-mls-medium`**, a Dutch Netherlands multi-speaker model, 52 speakers, 22,050 Hz. The app selects speaker 0.
- Model files and model card: https://huggingface.co/rhasspy/piper-voices/tree/main/nl/nl_NL/mls/medium
- Dataset: **Multilingual LibriSpeech (MLS)**, https://www.openslr.org/94/
- The model card identifies the dataset license as **CC BY 4.0**. Voice weights are redistributed unchanged.
- Piper implementation: https://github.com/OHF-Voice/piper1-gpl — **GPL-3.0**. A copy of its license is included with the standalone distribution.

## Runtime libraries

The standalone build contains dynamically linked libraries with their original notices/licenses, including:

| Component | License / project |
| --- | --- |
| CPython | PSF license, https://www.python.org/ |
| PySide6 / Qt | LGPLv3 / GPLv3 / commercial options, https://www.qt.io/qt-for-python ; unmodified dynamic libraries used here |
| CTranslate2 | MIT, https://github.com/OpenNMT/CTranslate2 |
| SentencePiece | Apache-2.0, https://github.com/google/sentencepiece |
| ONNX Runtime | MIT, https://github.com/microsoft/onnxruntime |
| NumPy | BSD-3-Clause, https://numpy.org/ |
| Pillow | HPND, https://python-pillow.org/ |
| PyWinRT | MIT, https://github.com/pywinrt/pywinrt |
| eSpeak NG (used by Piper) | GPL-3.0-or-later, https://github.com/espeak-ng/espeak-ng |
| PyInstaller | GPL-2.0-or-later with bootloader exception, https://pyinstaller.org/ |

Additional dependency notices are retained under `_internal/` and `licenses/` in the packaged app. The app source is provided under GPL-3.0-or-later; this does not relicense the separately licensed model weights or runtime components. The Qt libraries are unmodified and dynamically linked so users may replace compatible library builds.

## Optional online narration

- edge-tts (LGPL-3.0), https://github.com/rany2/edge-tts; unofficial Microsoft Edge speech client.
- miniaudio Python binding (MIT), https://github.com/irmen/pyminiaudio; decodes returned MP3 audio.
- aiohttp and related dependencies retain their licenses, bundled in `_internal/licenses`.
- Google Gemini, ElevenLabs and Azure APIs are external services governed by their own account terms. No API keys or provider voices are bundled.
