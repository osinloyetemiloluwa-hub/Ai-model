# ImageGen MCP Tool Integration

## Overview

CorvinOS ships with **ImageGen MCP Server** as a standard tool for AI-powered image generation. It integrates seamlessly with the OpenAI API (using the same key configured for Whisper/TTS) and supports multiple image generation models:

- **DALL-E 3** (OpenAI) — primary, highest quality
- **Flux 1.1** (Replicate) — state-of-the-art, cost-effective
- **Google Imagen 4** (Google Cloud) — alternative option

## Architecture

### MCP Server Configuration

The ImageGen MCP tool is configured in the persona definitions:

**File:** `operator/cowork/personas/assistant.json` and `operator/cowork/personas/forge.json`

```json
{
  "mcp_servers": {
    "imagegen": {
      "command": "npx",
      "args": ["-y", "imagegen-mcp-server@latest"],
      "env": {
        "OPENAI_API_KEY": "${OPENAI_API_KEY}"
      }
    }
  }
}
```

### API Key Reuse

The tool **automatically reuses the OpenAI API key** configured for Whisper/TTS (stored in `~/.config/corvin-voice/.env`). No additional configuration is needed.

- **Environment Variable:** `${OPENAI_API_KEY}` (automatically injected from the voice config)
- **Fallback:** If not set, the tool will check for `OPENAI_API_KEY` in the system environment
- **Scope:** Only applied when using the `imagegen` MCP server within a persona

## Usage

### In Claude Code (Assistant Persona)

When a user asks for an image or illustration, the assistant automatically invokes the ImageGen MCP tool:

```
User: "Generate an image of a surreal mouth floating in cosmic space"

Claude: [Calls imagegen MCP tool with DALL-E 3]
→ Image saved to ./outputs/image-<hash>.png
→ Automatically attached to Discord/email/etc.
```

### In Forge Persona

When forging tools that generate images as part of their output:

```python
# Inside a forged tool
import json
import urllib.request

# The imagegen MCP tool is available via the forge MCP server
# Request image generation from the running MCP server
```

### Model Selection

Default model: **DALL-E 3** (via OpenAI)

To use alternative models, pass the provider in the prompt or configure in the MCP tool settings:

- `provider: "openai"` → DALL-E 3
- `provider: "replicate"` → Flux 1.1 (requires Replicate API token in `REPLICATE_API_TOKEN`)
- `provider: "google"` → Imagen 4 (requires Google Cloud credentials)

## First Use Setup

When the ImageGen MCP tool is invoked for the first time in a chat:

1. **npx downloads** the latest `imagegen-mcp-server` package (~50 MB)
2. **One-time installation** — subsequent invocations reuse the cached binary
3. **API key validation** — confirms the OpenAI API key is valid (if using DALL-E)
4. **Service startup** — the MCP server runs as a subprocess during that chat session

**Expected first-use latency:** ~5–15 seconds (network + npm install)

## Configuration

### Per-Chat Override

To change the default provider for a specific chat, set an environment variable:

```bash
IMAGEGEN_PROVIDER=replicate claude code
```

### Persona-Level Override

To add ImageGen to a custom persona:

1. Edit `operator/cowork/personas/<name>.json`
2. Add to `mcp_servers`:
   ```json
   "imagegen": {
     "command": "npx",
     "args": ["-y", "imagegen-mcp-server@latest"],
     "env": {
       "OPENAI_API_KEY": "${OPENAI_API_KEY}",
       "IMAGEGEN_PROVIDER": "openai"
     }
   }
   ```
3. Restart the adapter or let hot-reload pick up the change

### Troubleshooting

**"Unknown model: 'dall-e-3'"**
- The OpenAI API key may not have access to Images API
- Verify the account has active billing and is not rate-limited
- Check: `curl https://api.openai.com/v1/images/generations -H "Authorization: Bearer $OPENAI_API_KEY" -d '{}' 2>&1 | jq .error`

**"OPENAI_API_KEY not set"**
- Verify `~/.config/corvin-voice/.env` contains `OPENAI_API_KEY=sk-...`
- Or export `OPENAI_API_KEY` in your shell before starting the adapter
- Check permissions: `ls -la ~/.config/corvin-voice/.env` (should be mode 0600)

**ImageGen MCP server crashes**
- Check for npm/Node.js version conflicts: `node --version` (should be ≥16)
- Clear npm cache: `npm cache clean --force`
- Try explicit version: `"args": ["-y", "imagegen-mcp-server@0.1.9"]`

## Repository Structure

```
operator/
└── cowork/
    └── personas/
        ├── assistant.json         ← ImageGen configured
        ├── forge.json             ← ImageGen configured
        └── ...

docs/
└── claude-ref/
    └── imagegen-mcp.md            ← This file
```

## References

- **Repository:** [writingmate/imagegen-mcp](https://github.com/writingmate/imagegen-mcp)
- **NPM Package:** [imagegen-mcp-server](https://www.npmjs.com/package/imagegen-mcp-server)
- **OpenAI Images API:** [docs.openai.com/api/images](https://platform.openai.com/docs/api-reference/images)

## Must NOT do

- Don't change the `env` variable name from `OPENAI_API_KEY` — it must match the voice config key name
- Don't add hard-coded API keys to persona JSON files (always use `${OPENAI_API_KEY}`)
- Don't add ImageGen to the `os` (admin) or `forge` personas without explicit ADR reasoning
- Don't fallback to deprecated script paths — ImageGen MCP is the only supported image-gen path going forward
