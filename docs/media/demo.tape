# docs/media/demo.tape: the 15-second README demo, recorded with VHS (https://github.com/charmbracelet/vhs).
# Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.
#
# What it shows (build spec F.7): "Harvest my TSLA loss" against the SANDBOX (synthetic household,
# no Robinhood account, no real orders) -> the three accounts are read -> the Roth IRA conflict line
# ($390.00 permanently disallowed, clean sale 2026-12-07) -> the order guard refusing a place call
# with exit code 2. The last beat pipes the exact event Claude Code sends into hooks/guard.sh,
# because the skill itself never calls an order tool, so a chat request would never reach the hook.
#
# How to record (from the repository root, on a machine WITHOUT your real Robinhood connector
# configured for this session; --strict-mcp-config below also keeps it out):
#   1. Install VHS and Claude Code, and sign in to Claude Code.
#   2. vhs docs/media/demo.tape            -> docs/media/demo.gif (and demo.mp4 for social posts)
#   3. Check the GIF: the caption line says "sandbox data", no Robinhood logo or colors appear,
#      no real account data appears, and the figures match evalkit/golden/demo.json
#      (python3 tools/check_readme.py checks the README line under the GIF).
#   4. Keep the GIF under about 5 MB (lower Framerate or raise PlaybackSpeed if needed).
#
# The model's wording differs from run to run; the figures don't (they come from the fixture and the
# skills' scripts). Hidden stretches cut the waiting out of the recording, so the GIF shows the prompt,
# then the finished answer. If a run answers differently than the example in
# docs/examples/harvest-check.md, re-record rather than edit the GIF.

Output docs/media/demo.gif
Output docs/media/demo.mp4

Require claude
Require python3

Set Shell "bash"
Set FontSize 22
Set Width 1280
Set Height 720
Set Padding 24
Set TypingSpeed 25ms
Set Framerate 20
Set PlaybackSpeed 1.0
Set Theme "Builtin Dark"
Set WindowBar Colorful
Set CursorBlink false

# Read-only sandbox tools the harvest check uses, plus the skill loader and the ways the skill feeds
# JSON to its bundled scripts. Order tools are deliberately NOT pre-approved; the hook would block
# them anyway. If a recording stalls at a permission prompt, add that exact rule here and re-record.
# Never pre-approve all of Bash or every MCP tool, even for a demo.
Env DEMO_ALLOW "Skill,Read,Bash(python3 *),Bash(echo *),Bash(printf *),Bash(cat *),Bash(cd *),mcp__rh-sandbox__get_accounts,mcp__rh-sandbox__get_equity_orders,mcp__rh-sandbox__get_equity_positions,mcp__rh-sandbox__get_equity_tax_lots,mcp__rh-sandbox__get_option_chains,mcp__rh-sandbox__get_option_orders,mcp__rh-sandbox__get_equity_quotes,mcp__rh-sandbox__get_pnl_trade_history,mcp__rh-sandbox__get_equity_fundamentals"
Env PS1 "$ "

Hide
Type "clear"
Enter
Show

# Beat 1: the caption and the command (about 3 s).
Type "# Preflight · sandbox data (synthetic household, no real account) · unofficial"
Enter
Sleep 700ms
Type 'claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config --allowedTools "$DEMO_ALLOW"'
Enter
Hide
Wait+Screen@60s /order mode|>|Try/
Sleep 2s
Show

# Beat 2: the request (about 4 s), then the finished answer (about 5 s on screen).
Type "(Context: it is Monday 2026-11-16, 8:05 PM ET.) Harvest my TSLA loss in my individual account: the 20 shares I bought in June."
Sleep 400ms
Enter
Hide
Wait+Screen@300s /manual ticket|tax-lot selector|Nothing was placed/
Sleep 3s
Show
Sleep 5s

# Leave Claude Code.
Hide
Type "/exit"
Enter
Sleep 2s
Type "clear"
Enter
Show

# Beat 3: the guard, fed the call a misled model would make (about 4 s).
Type `printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"mcp__rh-sandbox__place_equity_order","tool_input":{"symbol":"TSLA","side":"sell","type":"market","quantity":"20"},"session_id":"demo","permission_mode":"default"}' | sh hooks/guard.sh money; echo "exit=$?"`
Sleep 300ms
Enter
Wait+Screen@10s /exit=/
Sleep 3s

# Beat 4: the install line (about 2 s).
Type "npx skills add yashpatel-py/robinhood-trading-agent-skills   # unofficial"
Sleep 2s
