# 🎵 Lo-fi YouTube Automation — Project Ouroboros

> A self-improving AI organization that transforms MP3 files into
> fully produced YouTube videos with SEO-optimized metadata,
> AI-generated thumbnails, and YouTube Shorts — automatically.

## Quick Start

```bash
# 1. Clone and install
cd lofi-automation
pip install -r requirements.txt

# 2. Copy .env.example to .env and fill in your API keys
cp .env.example .env

# 3. Drop an MP3 in the audio/ folder and run
python main.py audio/my_song.mp3
```

## Architecture

```
You (Telegram) → GitHub Push → GitHub Actions → Pipeline → YouTube Draft
```

### The 10-Agent Organization

| # | Agent | Role |
|---|---|---|
| 1 | Director | Listens to audio, produces creative brief |
| 2 | Video Editor | FFmpeg render with audio-reactive visualizer |
| 3 | Marketer | SEO titles, Lore-Fi descriptions, hashtags |
| 4 | Thumbnail Creator | AI image + branded typography |
| 5 | QA Tester | Validates all outputs before upload |
| 6 | Compliance | Scans for YouTube policy violations |
| 7 | Compiler | Weekly compilation mixes (coming soon) |
| 8 | Ghostwriter | Auto-replies to comments (coming soon) |
| 9 | Scout | Competitor intelligence (coming soon) |
| 10 | Distributor | Auto-generates YouTube Shorts |

### Self-Improvement Loop

Each agent stores learned rules in `memory/*.json`. These files are
committed back to the repo after each run, giving the system persistent,
version-controlled memory that improves quality over time.

## Project Structure

```
lofi-automation/
├── agents/                    # The 10 AI agents
│   ├── director.py            # Audio analysis via Gemini
│   ├── video_editor.py        # FFmpeg assembly engine
│   ├── marketer.py            # SEO + Lore-Fi descriptions
│   ├── thumbnail_creator.py   # AI image + Pillow typography
│   ├── qa_tester.py           # Quality validation
│   ├── compliance.py          # Policy safety check
│   ├── distributor.py         # YouTube Shorts factory
│   └── background_fetcher.py  # Pexels + Pixabay video fetch
├── core/                      # Shared infrastructure
│   ├── config.py              # Configuration loader
│   ├── api_rotation.py        # Multi-key rotation (6 Gemini + 5 Groq)
│   ├── memory.py              # Persistent JSON memory system
│   └── discord_webhook.py     # Discord notifications
├── memory/                    # Agent memory (auto-generated)
├── output/                    # Rendered videos + thumbnails
├── temp/                      # Temporary downloads
├── templates/fonts/           # Brand fonts (add .ttf files here)
├── brand_config.json          # Dual-channel brand settings
├── main.py                    # Pipeline orchestrator
├── requirements.txt
└── .github/workflows/
    └── pipeline.yml           # GitHub Actions automation
```

## API Keys Required

| Service | Keys | Purpose |
|---|---|---|
| Gemini | 6 | Audio analysis, SEO, compliance |
| Groq | 5 | Fallback LLM for text generation |
| Pexels | 1 | Background video downloads |
| Pixabay | 1 | Fallback background videos |
| Discord | 3 | Webhook notifications |

Thumbnail generation uses Pollinations.ai (free, no key needed).

## Usage

```bash
# Process a single MP3
python main.py audio/sad_rain.mp3

# Process all MP3s in the audio/ folder
python main.py audio/

# Use the Hindi channel config
python main.py audio/ --channel hindi

# Skip Shorts generation
python main.py audio/ --no-shorts

# Skip Discord notifications
python main.py audio/ --no-discord
```

## License

MIT
