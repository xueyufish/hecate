# Configure Feishu and Slack IM Channels

This guide walks through connecting Hecate to a Feishu (Lark) bot or a
Slack app so that messages from those platforms are routed into Hecate
Agents.

## Prerequisites

- A running Hecate deployment reachable from the public internet (or via
  ngrok / a similar tunnel during development)
- A workspace in Hecate with at least one user (the binding workflow
  requires a Hecate user to bind the IM identity to)
- Python dependencies installed: `uv pip install "hecate[tools]"`

## Feishu (Lark)

### 1. Create a Feishu App

1. Open the [Feishu Open Platform developer console](https://open.feishu.cn/app).
2. Click **Create App** → **Custom App**.
3. Fill in the app name and description.
4. On the **Permissions** page, grant at least:
   - `im:message` — receive message events
   - `im:message:send_as_bot` — send messages as the bot
   - `im:message.p2p_msg` (optional) — receive direct messages
   - `im:message.group_at_msg` (optional) — receive @-mentions in groups
5. Note the **App ID** and **App Secret** under **Credentials & Basic Info**.
6. Under **Event Subscriptions**, choose **Webhook** (or **Long Connection** for
   `transport=ws`).
7. Set the **Request URL** to:
   ```
   https://<your-hecate-host>/v1/channels/feishu/webhook
   ```
   Feishu will send a verification challenge; Hecate returns 200 OK.
8. Subscribe to the `im.message.receive_v1` event.
9. If encryption is desired, copy the **Encrypt Key** and **Verification Token**.
10. Publish a version and have your workspace admin approve the app.

### 2. Configure Hecate

Set the environment variables in `.env`:

```bash
HECATE_IM_FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
HECATE_IM_FEISHU_APP_SECRET=your_app_secret
HECATE_IM_FEISHU_ENCRYPT_KEY=   # optional
HECATE_IM_FEISHU_VERIFICATION_TOKEN=   # optional
HECATE_IM_FEISHU_TRANSPORT=webhook
```

Restart Hecate. Logs should include:

```
Registered Feishu IM channel adapter
IM channels initialized: 1 IM adapter(s) registered
```

### 3. Bind your IM identity

The first time you DM the bot, it will reply with a binding URL like:

```
https://<your-hecate-host>/v1/im/bindings/confirm?token=...
```

Click the link, log into Hecate, and confirm. Subsequent messages will
be routed to the Agent.

## Slack

### 1. Create a Slack App

1. Open [api.slack.com/apps](https://api.slack.com/apps) and click
   **Create New App** → **From scratch**.
2. Name the app and pick a workspace.
3. Under **OAuth & Permissions**, add the following Bot Token scopes:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `channels:read`
   - `groups:history`
   - `groups:read`
   - `im:history`
   - `im:read`
   - `im:write`
   - `users:read`
4. Install the app to your workspace. Note the **Bot User OAuth Token**
   (`xoxb-...`).
5. Under **Basic Information**, copy the **Signing Secret**.
6. Under **Event Subscriptions**, enable events. Set the Request URL to:
   ```
   https://<your-hecate-host>/v1/channels/slack/webhook
   ```
7. Subscribe to Bot Events:
   - `app_mention`
   - `message.channels`
   - `message.im`
   - `message.mpim`
8. (Optional) For Socket Mode, enable it under **Socket Mode** and copy
   the **App-Level Token** (`xapp-...`).

### 2. Configure Hecate

```bash
HECATE_IM_SLACK_BOT_TOKEN=xoxb-...
HECATE_IM_SLACK_SIGNING_SECRET=your_signing_secret
HECATE_IM_SLACK_APP_TOKEN=xapp-...   # optional, only for Socket Mode
```

Restart Hecate. Logs should include the Slack registration line.

### 3. Bind your IM identity

DM the bot (`@<your-bot-name>` in a channel, or open a DM). Hecate will
reply with a binding URL — same flow as Feishu.

## Channel discovery (PR5a)

Both Feishu and Slack are registered automatically as entry points under
`hecate.channel_providers` in the root `pyproject.toml`. The `Settings`
field `CHANNEL_PROVIDERS` (default `("feishu", "slack")`) controls which
named channels are loaded at boot — the resolver reads the tuple and
ignores any installed entry whose name is not listed. To disable a
channel without uninstalling its package, simply remove it from the
tuple (e.g., `CHANNEL_PROVIDERS=("slack",)` keeps Slack only).

Each factory reads its own `HECATE_IM_*` env vars and returns `None`
when unconfigured — the resolver skips `None` without raising, so a
partial setup boots cleanly. If the entry-point metadata is unavailable
(e.g. unusual packaging), `register_im_channels` falls back to the
historical env-gated soft-import path automatically.

## Troubleshooting

### Webhook returns 503

```
IM channels initialized: 0 IM adapter(s) registered
```

Either credentials are missing or the `[tools]` extras are not
installed. Check `pip show lark-oapi` and `pip show slack-bolt`.

### Signature verification fails

Verify that the Signing Secret / Encrypt Key values in `.env` match
those in the IM platform's app settings. Webhook URLs must use HTTPS in
production.

### Bind URL returns 410 Gone

The token is single-use and expires after 10 minutes. Request a new one
by sending another message to the bot.

### Cross-workspace leakage

Each binding is scoped by `workspace_id`. If you operate multiple
workspaces, each must use its own Feishu / Slack App credentials.