# Key & Secret Locations (gitignored — never commit this file)

This document only records **where** credentials live and what they're for.
It contains no private-key material, tokens, or passwords.

## SSH keys (`~/.ssh/`)
- `pleiades-mini-key` (+ `.pub`)
  Private key to SSH into the **micro / control plane**: `ubuntu@10.0.0.22`
  (tailnet) or `ubuntu@141.147.60.138` (public). This VPS *provisioned* the
  micro, so this VPS is the origin of the micro's authorized key — i.e. the
  micro trusts this key. Verified working.
  Usage: `ssh -i ~/.ssh/pleiades-mini-key ubuntu@10.0.0.22`
- `id_ed25519` (symlink → `/home/ubuntu/code/testing_hy3/id_ed25519`)
  This executor VPS's own SSH identity.
- `authorized_keys`
  Public keys permitted to log into this executor VPS.

## YouTube cookies
- `pleiades/www.youtube.cookies.txt`
  Exported YouTube cookies consumed by yt-dlp (gitignored via `*cookies*.txt`).
  Last refreshed 2026-07-14. Read by the streamer through `YOUTUBE_COOKIES_PATH`.
  Used together with the Deno-generated PoToken (JS challenge solver).

## Databases
- Metadata (videos/channels/etc.): `postgresql://pleiades:***@127.0.0.1:5432/pleiades`
  (full URL in `.env`, gitignored).
- Artifact vault: HuggingFace `Rolaficus/pleiades-vault-clean`
  (`VAULT_PROVIDER=huggingface`, full id in `.env`).

## YouTube Data API key pool
- 24 keys in `YOUTUBE_API_KEY_POOL_JSON` (in `.env`, gitignored).
  Used by the archeologist/hunter for discovery; currently quota-exhausted
  (state in `/var/lib/pleiades/agent_state.json`).

## Cross-process agent state
- `/var/lib/pleiades/agent_state.json`
  `quota_exhausted`, `audio_usage`, and (was) `rate_limit` back-off markers.
  Not a secret; lives outside the repo.

## Prefect control plane (micro)
- API: `http://10.0.0.22:4200/api` (tailnet) — reachable from this VPS.
  Local `.env` `PREFECT_API_URL` points at `prefect.cloud`; the live worker
  reports to the micro.
