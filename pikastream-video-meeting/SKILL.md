---
name: pikastream-video-meeting
description: |
  Join a Google Meet or Zoom call as a video meeting agent via PikaStreaming.
  Trigger: user drops a Google Meet or Zoom link, or asks to join a meeting.
metadata:
  openclaw:
    requires:
      env: ["PIKA_DEV_KEY"]
      bins: ["python3"]
    primaryEnv: "PIKA_DEV_KEY"
---

# PikaStream Video Meeting

Script: `SKILL_DIR=skills/pikastream-video-meeting`

## First-Time Setup

Run once when the skill is first loaded:

```bash
pip install -r $SKILL_DIR/requirements.txt
```

### 1. Avatar

Check if `identity/videomeeting-avatar.png` exists and is larger than 1 KB. If it does NOT exist (or is too small):

1. Ask the user: `I need an avatar image for the video meeting (a headshot or portrait). Send me an image, or say "generate" and I'll create one for you.`
2. **Do not proceed until the user responds.** Do not auto-generate.
3. **User sends an image:** save it to `identity/videomeeting-avatar.png`.
4. **User says "generate":** run:
   ```bash
   python $SKILL_DIR/scripts/pikastreaming_videomeeting.py generate-avatar \
     --output identity/videomeeting-avatar.png
   ```
   If the user describes what they want (e.g. "a cartoon cat"), pass `--prompt "<description>, portrait headshot suitable for video calls"`.
   Show the generated image. Ask: `Want to keep this avatar or regenerate?` Wait for reply.
5. **Anything else:** repeat the question from step 1.

The bot must have an avatar before joining a meeting.

### 2. Voice

Check if `life/voice_id.txt` exists and is non-empty.

**If it exists:** read `life/voice_config.json`. If readable, check `cloned_at` — if 6+ days ago, warn the user:
`Your voice clone was created on {date} and may have expired (cloned voices are deleted after 7 days of non-use). Want to re-clone with a new recording, or try the existing one?`
- Re-clone: go to "If it does not exist" below.
- Keep it: use the existing voice ID.

If `life/voice_config.json` is missing or unreadable, use the voice ID from `life/voice_id.txt` as-is.

**If it does not exist** (or user chose to re-clone):

1. Ask the user: `I don't have a voice clone yet. You can: (a) send me a voice recording (10s-5min, clear speech) and I'll clone it, or (b) say "skip" to use a default voice.`
2. **Do not proceed until the user responds.**
3. **User says "skip":** use `English_radiant_girl`.
4. **User sends an audio file:** run:
   ```bash
   python $SKILL_DIR/scripts/pikastreaming_videomeeting.py clone-voice \
     --audio <file> --name <bot-name> --noise-reduction
   ```
   - Exit 0: read `life/voice_id.txt`. Tell user: `Voice cloned. Using {voice_id} for this meeting.`
   - Exit non-zero: tell user cloning failed (include stderr). Ask: `Try again with a different file, or skip and use the default voice?`
5. **Invalid file (not audio):** respond `That doesn't look like a supported audio file. Send an mp3, m4a, wav, ogg, flac, or aac file (10s-5min of clear speech).` Wait for retry or "skip".

---

## Join Flow

### Step 1 — Validate & gather context

**Avatar:** check `identity/videomeeting-avatar.png` exists and is > 1 KB. If not, run First-Time Setup above.

**Voice:** check `life/voice_id.txt` exists. If not, run the Voice section of First-Time Setup above.

**Context:** always gather fresh context — do not reuse a stale file from a previous session.

1. Read your workspace files (MEMORY.md, daily logs, identity files, etc.).
2. If no workspace data is available, ask the user: `What name should the bot use in this meeting?` Use their answer for the `Name:` field. Fill in any other sections you know from the conversation.
3. Synthesize a concise reference card to `/tmp/meeting_system_prompt.txt`. Use `{name}` as the bot's display name (also used as `--bot-name`). If data is thin (e.g. only a name), keep it short — don't pad with filler.

```
Synthesize the raw data below into a concise reference card for {name} to use during a voice/video call. Use third-person ("{name}") throughout. Prioritize CONCRETE DETAILS.

PRIORITY ORDER:
1. SPECIFIC FACTS: names, places, dates, numbers, events
2. RECENT ACTIVITY: what happened today/this week — actions, not vibes
3. RELATIONSHIPS: who matters, specific interactions
4. PERSONALITY: 1-2 sentences MAX

CURATION RULES:
- KEEP: anything with a proper noun, a number, a date, or a concrete action
- DROP: vague descriptions, routine status updates, empty entries
- MERGE: if multiple entries say similar things, pick the most vivid one

OUTPUT FORMAT:

**{name}**: [1 sentence — tone/vibe]

**Known facts** (concrete only, max 10):
- [specific fact with names/dates/numbers]

**Recent activity**:
- [built X, fixed Y, went to Z]

**Right now**: [1 line — current activity]

**People**: [name — 1 specific detail each]

RULES:
- Concrete > abstract
- Actions > descriptions
- Do not invent facts
- If data is thin, keep it short
```

### Step 2 — Join

```bash
python $SKILL_DIR/scripts/pikastreaming_videomeeting.py join \
  --meet-url <url> --bot-name <name> \
  --image identity/videomeeting-avatar.png \
  --system-prompt-file /tmp/meeting_system_prompt.txt \
  --voice-id <id> [--meeting-password <pw>]
```

Tell the user you're in. Say `leave` to leave. Don't mention session IDs.

**Exit codes:** 0 = joined. 6 = insufficient credits (stdout JSON contains a `checkout_url` — show it to the user).

## Leave

```bash
python $SKILL_DIR/scripts/pikastreaming_videomeeting.py leave \
  --session-id <id from join output>
```
