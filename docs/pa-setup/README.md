# PA Machine Setup — Claude Code for Winfred's Assistant

Purpose: give the assistant full Claude Code capability (docs, research, repo work, drafting)
while making it impossible for her sessions to clash with the live WhatsApp automation on
Winfred's Mac. One sender, always.

## What she gets
- WhatsApp via web.whatsapp.com in her browser (read + reply as Winfred; the intake bot
  automatically goes silent on any chat she replies to by hand)
- Claude Code with the full repo, bound by the rules files below
- Drafting workflows: Claude writes, she sends by hand from WhatsApp Web

## What her machine can never do (enforced, not honour system)
- Send WhatsApp messages from Claude (send tools + bridge API denied in settings;
  the bridge is also bound to localhost on Winfred's Mac, unreachable from her machine)
- Push to main (deploys production), touch launchd jobs, or handle credentials

## Setup steps (15 minutes)

1. Install Claude Code on her machine and sign in with HER account (never Winfred's).
2. Clone the repo: `git clone <repo url> ~/crestbrick-consult`
   (its CLAUDE.md carries the CEA role boundary + single sender doctrine automatically).
3. Copy the two files from this folder into place:
   - `claude-user-settings.json`  ->  `~/.claude/settings.json`
   - `claude-user-CLAUDE.md`      ->  `~/.claude/CLAUDE.md`
   If she already has a settings.json, merge the `permissions.deny` list into it.
4. Log her into web.whatsapp.com with Winfred's phone (Linked Devices > Link a Device).
5. GitHub: add her account with WRITE (not admin) on the repo; turn on branch protection
   for `main` (require pull request) at github.com > repo Settings > Branches.
6. Do NOT configure any WhatsApp MCP server on her machine. If one is ever added later,
   the deny list in step 3 already blocks its send tools.
7. Never copy over: `.env` files, `~/.telegram-bot.env`, `~/.pg-agent.env`, API keys,
   or anything under `~/.claude/state` from Winfred's Mac.

## Day one smoke test
Ask her Claude: "send a whatsapp message to <any number> saying test" — it must refuse
(no tool + denied permission). Ask it to DRAFT a follow-up message instead — it should
write one for her to send from WhatsApp Web. Ask it to `git push origin main` — denied.

## The one rule that matters
If a task looks like it needs automation that MESSAGES anyone: stop, flag Winfred.
The engine on his Mac gets extended instead. No second senders, no parallel intake forms.
