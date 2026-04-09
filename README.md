# Pika Skills Open

A collection of open-source skills for AI coding agents (Claude Code, OpenClaw, etc.) powered by the [Pika Developer API](https://www.pika.me/dev/).

## What are Skills?

Skills are self-contained modules that extend the capabilities of AI coding agents. Each skill is a directory containing:

- **`SKILL.md`** — The skill definition file. It tells the AI agent *when* to activate the skill, *how* to use it, and includes step-by-step instructions. The agent reads this file and follows the workflow described in it.
- **`scripts/`** — Executable scripts (Python, Bash, etc.) that the agent invokes as part of the skill workflow.
- **`requirements.txt`** — Python dependencies needed by the skill scripts.

When you install a skill into your agent workspace, the agent automatically detects the `SKILL.md` and knows how to use the skill — no manual configuration needed.

## Available Skills

| Skill | Pricing | Description |
|-------|---------|-------------|
| [pikastream-video-meeting](pikastream-video-meeting/) | $0.275 / min | Join a Google Meet as a real-time AI avatar. |
| [mnemopay-billing](skills/mnemopay-billing/) | Pay-per-use | Agent FICO credit scoring and charge/settle/refund billing for paid skills. |

## Getting Started

### 1. Get a Pika Developer Key

Go to [https://www.pika.me/dev/](https://www.pika.me/dev/) and create a Developer Key (starts with `dk_`).

### 2. Set the Environment Variable

```bash
export PIKA_DEV_KEY="dk_your-key-here"
```

Or add it to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.) for persistence.

### 3. Install a Skill

Point your agent to the skill folder and ask it to install. For example:

> install /path/to/your/Pika-Skills/pikastream-video-meeting/

### 4. Use It

Once installed, simply interact with your AI agent naturally. For example, drop a Google Meet link and the agent will automatically activate the `pikastream-video-meeting` skill.

The skill will check your balance before joining. If you need credits, it will generate a payment link for you automatically.

## Skill: pikastream-video-meeting

An AI-powered video meeting bot that joins Google Meet calls as a real-time avatar.

### Features

- **Real-time avatar** — Joins meetings with a generated or custom avatar image via PikaStreaming.
- **Voice cloning** — Clone your voice from a short audio recording.
- **Avatar generation** — Generate an AI avatar via OpenAI image models, or provide your own image.
- **Automatic billing** — Checks balance before joining, creates a payment link if needed, and waits for payment to complete.
- **Context-aware conversation** — The bot synthesizes workspace context (identity, recent activity, known people) into a system prompt for natural, informed conversation during meetings.
- **Post-meeting notes** — Automatically retrieves and shares meeting notes after the bot leaves.

### Commands

The skill script supports four subcommands:

```bash
# Join a Google Meet
python scripts/pikastreaming_videomeeting.py join \
  --meet-url <google-meet-url> --bot-name <name> \
  --image <avatar-image> [--voice-id <id>] [--system-prompt-file <path>]

# Leave a meeting
python scripts/pikastreaming_videomeeting.py leave --session-id <id>

# Generate an avatar image
python scripts/pikastreaming_videomeeting.py generate-avatar --output <path> [--prompt <text>]

# Clone a voice from audio
python scripts/pikastreaming_videomeeting.py clone-voice \
  --audio <file> --name <name> [--noise-reduction]
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PIKA_DEV_KEY` | Yes | Pika Developer Key (`dk_...`). Get one at [pika.me/dev](https://www.pika.me/dev/) |

### Requirements

- Python 3.10+
- `PIKA_DEV_KEY` environment variable
- `ffmpeg` (optional, for audio format conversion during voice cloning)

## Contributing

To add a new skill:

1. Create a new directory with your skill name.
2. Add a `SKILL.md` following the frontmatter format (see existing skills for reference).
3. Add scripts and a `requirements.txt` as needed.
4. Update this README with your skill's description.

## License

Apache 2.0 — see [LICENSE](LICENSE).
