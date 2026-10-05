# `forgeddit`

Scrub your Reddit history using your GDPR data export. One file, no setup beyond [`uv`](https://docs.astral.sh/uv/).

Reddit's API only lists your ~1000 newest items, but the export has the ID of everything you've ever posted. `forgeddit` reads those IDs straight from the zip, overwrites each item with junk text, then deletes it.

## Create Reddit API credentials

1. Go to https://old.reddit.com/prefs/apps while logged in
2. Click **create another app...**
3. Name it anything
4. Select **script**
5. Set the redirect URL to `localhost:8080`
6. Click **create app**

**Client ID** is the string shown under the app name - click **edit** to reveal the **client secret**

For two-factor auth, append the current code to your password like so: `yourpassword:123456`

## Get GDPR data

1. Request data from Reddit: https://reddit.zendesk.com/hc/en-us/articles/360043048352-How-do-I-request-a-copy-of-my-Reddit-data-and-information-
2. When it arrives, download it (no need to unzip)

## Install `uv`

```
brew install uv                                   # macOS
curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux
```

## Run

Put your credentials in a private env file:

```
umask 077
cat > ~/.forgeddit.env <<'EOF'
REDDIT_CLIENT_ID=xxxx
REDDIT_CLIENT_SECRET=xxxx
REDDIT_USERNAME=yourname
EOF
```

Always dry-run first (needs no credentials or network):

```
uv run forgeddit.py export_yourname_20261004.zip --dry-run
```

Then for real. Type your password (plus 2FA code) when prompted by `read`:

```
read -rs REDDIT_PASSWORD && export REDDIT_PASSWORD
uv run --env-file ~/.forgeddit.env forgeddit.py export_yourname_20261004.zip
```

## Options

| Flag | Effect |
| --- | --- |
| `--dry-run` | Show what would happen, change nothing |
| `--yes` | Skip the `DELETE` confirmation prompt |
| `--older-than DAYS` | Only touch items older than this |
| `--include-subreddits a,b` | Only these subreddits |
| `--exclude-subreddits a,b` | Never touch these subreddits |
| `--no-overwrite` | Delete without overwriting first |
| `--votes` | Also clear up/downvotes (slow) |
| `--saved` | Also unsave saved posts and comments |
| `--delay SECONDS` | Extra pause between actions |

## Notes

- Safe to interrupt and re-run. Already-deleted items just report `gone`.
- Reddit's rate limit is the bottleneck: expect roughly 50 items per minute.
- Reddit blocks edits on items older than about 6 months, so those are deleted without being overwritten.
- Chats and private messages are not touched.
- Third-party archives may already hold copies of your content.
- When you're done, delete the app at https://old.reddit.com/prefs/apps and remove `~/.forgeddit.env`.
