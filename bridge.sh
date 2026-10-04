#!/usr/bin/env bash
# agmsg-bridge — an optional autonomous delivery layer for agmsg.
#
# NOTE (agent2claw): this project does not run the loop below as a daemon.
# runtime.py spawns this script once per message with AGMSG_BRIDGE_MAX_DISPATCH=1
# and AGMSG_BRIDGE_MAX_BOT_HOPS=0, then kills the whole process group after the
# reply is delivered. See run_once() in runtime.py.
#
# agmsg (https://github.com/fujibee/agmsg) is a shared SQLite inbox: agents send and check
# messages on demand, with no daemon. That is great for agents that poll their
# own mail, but an agent with no agmsg hook of its own never sees a message —
# it just sits unread. This bridge is the missing mail carrier: it polls the
# inbox, invokes the right agent through a per-agent adapter, captures the
# reply, and posts it back. The result is autonomous real-time delivery, with
# caps so two bots can't loop forever.
#
# Built on top of agmsg. See README for the credit + design notes.
set -uo pipefail

# Optional: load a secrets/env file your adapters need (API keys, tokens, paths).
# shellcheck disable=SC1090 # The operator supplies this optional runtime path.
[ -n "${AGMSG_BRIDGE_ENV:-}" ] && [ -r "${AGMSG_BRIDGE_ENV}" ] && . "${AGMSG_BRIDGE_ENV}"

# ---- config (override via env / AGMSG_BRIDGE_ENV) -------------------------
AGMSG_DIR="${AGMSG_DIR:-$HOME/.agents/skills/agmsg}"
DB="${AGMSG_DB:-$AGMSG_DIR/db/messages.db}"
TEAM="${AGMSG_BRIDGE_TEAM:-team}"
SERVED="${AGMSG_BRIDGE_AGENTS:-}"               # space-separated agents to answer for
ADAPTERS="${AGMSG_BRIDGE_ADAPTERS:-$(cd "$(dirname "$0")" && pwd)/adapters}"
STATE="${AGMSG_BRIDGE_STATE:-$(cd "$(dirname "$0")" && pwd)/state}"
HTTP_STATUS="$(cd "$(dirname "$0")" && pwd)/http_server.py"

POLL_INTERVAL="${AGMSG_BRIDGE_POLL:-4}"         # seconds between inbox scans
WINDOW="${AGMSG_BRIDGE_WINDOW:-300}"            # rate-limit window (s)
MAX_DISPATCH="${AGMSG_BRIDGE_MAX_DISPATCH:-12}" # max total auto-invokes / window
MAX_BOT_HOPS="${AGMSG_BRIDGE_MAX_BOT_HOPS:-6}"  # max bot<->bot exchanges / window
ADAPTER_TIMEOUT="${AGMSG_BRIDGE_ADAPTER_TIMEOUT:-300}" # hard kill on a slow adapter

mkdir -p "$STATE"
US=$'\x1f'
log(){ echo "$(date -u +%FT%TZ) [bridge] $*"; }
valid_name(){ case "$1" in ''|*[!A-Za-z0-9_.-]*) return 1;; esac; }
track_status(){ # $1=id $2=status [$3=error]; completed reply arrives on stdin
  if [ "$#" -eq 3 ]; then
    python3 "$HTTP_STATUS" --db "$DB" --update-status "$1" --status "$2" --error "$3"
  else
    python3 "$HTTP_STATUS" --db "$DB" --update-status "$1" --status "$2"
  fi >/dev/null 2>&1 || log "ERROR updating HTTP status for msg $1"
}
send_reply(){ # $1=file $2=from $3=to
  python3 - "$DB" "$TEAM" "$1" "$2" "$3" <<'PY'
import sqlite3
import sys
from pathlib import Path

body = Path(sys.argv[3]).read_bytes().decode("utf-8")
with sqlite3.connect(sys.argv[1]) as db:
    db.execute(
        "INSERT INTO messages(team,from_agent,to_agent,body) VALUES(?,?,?,?)",
        (sys.argv[2], sys.argv[4], sys.argv[5], body),
    )
PY
}
validate_reply(){ # $1=file; 0=valid, 3=empty, 4=too large, 5=invalid text
  python3 - "$1" <<'PY'
import sys
from pathlib import Path

data = Path(sys.argv[1]).read_bytes()
if not data:
    raise SystemExit(3)
if len(data) > 8192:
    raise SystemExit(4)
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    raise SystemExit(5)
if any((ord(char) < 32 and char not in "\t\n\r") or ord(char) == 127 for char in text):
    raise SystemExit(5)
PY
}

# ---- rate / loop guards ---------------------------------------------------
_prune(){ # $1 = state file; keep only timestamps within WINDOW
  local f="$1" now; now=$(date +%s); touch "$f"
  awk -v now="$now" -v win="$WINDOW" '$1 > now-win' "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f"
}
dispatch_allowed(){ _prune "$STATE/dispatches"; [ "$(wc -l < "$STATE/dispatches")" -lt "$MAX_DISPATCH" ]; }
record_dispatch(){ date +%s >> "$STATE/dispatches"; }
bot_hops_allowed(){ _prune "$STATE/bothops"; [ "$(wc -l < "$STATE/bothops")" -lt "$MAX_BOT_HOPS" ]; }
record_bot_hop(){ date +%s >> "$STATE/bothops"; }
is_served(){ case " $SERVED " in *" $1 "*) return 0;; *) return 1;; esac; }

# ---- adapter invocation ---------------------------------------------------
# An adapter is any executable at $ADAPTERS/<agent>.sh that takes the path of
# a private (0600) prompt file as $1 and prints the agent's reply to stdout
# (empty stdout = no reply). The prompt file keeps private message text off
# command lines, where other local users could read it via ps(1). Adding a
# new agent is just dropping in one adapter file. Examples live in adapters/.
invoke_agent(){
  local agent="$1" prompt="$2" adapter="$ADAPTERS/$1.sh" prompt_file
  [ -x "$adapter" ] || { log "no executable adapter for '$agent' at $adapter" >&2; return 0; }
  prompt_file=$(mktemp "$STATE/prompt.$agent.XXXXXX") || { log "bridge: cannot create private prompt file for '$agent'" >&2; return 1; }
  chmod 600 "$prompt_file" || { log "bridge: cannot secure private prompt file for '$agent'" >&2; rm -f "$prompt_file"; return 1; }
  printf '%s' "$prompt" >"$prompt_file" || { log "bridge: cannot write private prompt file for '$agent'" >&2; rm -f "$prompt_file"; return 1; }
  python3 - "$ADAPTER_TIMEOUT" "$adapter" "$prompt_file" <<'PY'
import math
import os
import signal
import subprocess
import sys

try:
    timeout = float(sys.argv[1])
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError
except ValueError:
    print("bridge: invalid adapter timeout", file=sys.stderr)
    raise SystemExit(2)

adapter, prompt_file = sys.argv[2], sys.argv[3]

try:
    process = subprocess.Popen([adapter, prompt_file], start_new_session=True)
except OSError:
    print("bridge: cannot launch adapter", file=sys.stderr)
    raise SystemExit(1)


def stop_process_group(signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def relay_signal(signum, _frame):
    stop_process_group(signum)
    raise SystemExit(128 + signum)


for handled_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(handled_signal, relay_signal)

try:
    raise SystemExit(process.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    stop_process_group(signal.SIGTERM)
    print("bridge: adapter timed out", file=sys.stderr)
    raise SystemExit(124)
finally:
    # The prompt file holds private message text: remove it on every exit
    # path so it never lingers past the adapter invocation.
    try:
        os.unlink(prompt_file)
    except OSError:
        pass
PY
  status=$?
  # Belt and braces with the Python finally above: the file must not survive
  # this function no matter how the interpreter below exits.
  rm -f "$prompt_file"
  return $status
}

# Dispatch one message in the background so a slow agent never blocks delivery
# to a faster one. An $STATE/inflight.<agent> lock caps each agent to one
# in-flight turn at a time.
dispatch_async(){
  local agent="$1" id="$2" from="$3" body="$4" tracked="$5" prompt reply_file validation
  prompt="Message from '$from' via agmsg: $body"
  (
    reply_file=$(mktemp "$STATE/reply.$agent.XXXXXX") || {
      [ "$tracked" = 1 ] && track_status "$id" failed adapter_failed </dev/null
      log "cannot create private reply file for $agent on msg $id"
      rm -f "$STATE/inflight.$agent"
      exit 0
    }
    chmod 600 "$reply_file"
    trap 'rm -f "$reply_file" "$STATE/inflight.$agent"' EXIT HUP INT TERM
    [ "$tracked" = 1 ] && track_status "$id" processing </dev/null
    if ! invoke_agent "$agent" "$prompt" >"$reply_file"; then
      [ "$tracked" = 1 ] && track_status "$id" failed adapter_failed </dev/null
      log "adapter failed for $agent on msg $id"
      exit 0
    fi
    validation=0
    validate_reply "$reply_file" || validation=$?
    case "$validation" in
      0)
      if send_reply "$reply_file" "$agent" "$from" >/dev/null 2>&1; then
        [ "$tracked" = 1 ] && track_status "$id" completed <"$reply_file"
        log "reply delivered $agent -> $from"
      else
        [ "$tracked" = 1 ] && track_status "$id" failed reply_delivery_failed </dev/null
        log "ERROR posting reply $agent -> $from"
      fi
      ;;
      3)
        [ "$tracked" = 1 ] && track_status "$id" failed no_reply </dev/null
        log "no reply captured from $agent for msg $id"
      ;;
      4)
        [ "$tracked" = 1 ] && track_status "$id" failed reply_too_large </dev/null
        log "reply too large from $agent for msg $id"
      ;;
      *)
        [ "$tracked" = 1 ] && track_status "$id" failed invalid_reply </dev/null
        log "invalid reply text from $agent for msg $id"
      ;;
    esac
  ) &
}

# ---- main loop ------------------------------------------------------------
rm -f "$STATE"/inflight.* 2>/dev/null   # clear stale locks from a prior run
log "starting: team=$TEAM served=[$SERVED] poll=${POLL_INTERVAL}s db=$DB"
[ -n "$SERVED" ] || { log "FATAL: set AGMSG_BRIDGE_AGENTS to the agents to serve"; exit 1; }
[ -f "$DB" ] || { log "FATAL: agmsg db not found at $DB"; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { log "FATAL: sqlite3 not found on PATH"; exit 1; }
# The claim query below relies on UPDATE ... RETURNING (SQLite 3.35+). Probe
# for it once instead of silently failing on every poll.
sqlite3 :memory: "CREATE TABLE probe(x); INSERT INTO probe VALUES(1) RETURNING x;" >/dev/null 2>&1 \
  || { log "FATAL: sqlite3 lacks RETURNING support (SQLite 3.35 or newer is required)"; exit 1; }
valid_name "$TEAM" || { log "FATAL: invalid team name"; exit 1; }
for agent in $SERVED; do valid_name "$agent" || { log "FATAL: invalid served agent name"; exit 1; }; done

while true; do
  for agent in $SERVED; do
    # one turn per agent at a time; leave the msg unclaimed so it retries
    [ -f "$STATE/inflight.$agent" ] && continue

    # Claim and return one row atomically. Two bridge processes cannot dispatch
    # the same message, because only one UPDATE can change read_at from NULL.
    row=$(sqlite3 -separator "$US" "$DB" \
      "UPDATE messages SET read_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
       WHERE id=(SELECT id FROM messages
         WHERE team='$TEAM' AND to_agent='$agent' AND from_agent != '$agent'
           AND read_at IS NULL ORDER BY id ASC LIMIT 1)
         AND read_at IS NULL
       RETURNING id,from_agent,body;" 2>/dev/null)
    [ -z "$row" ] && continue

    # Splitting on US (0x1f) is safe only because http_server.py rejects
    # control characters in bodies and validates agent names against NAME_RE.
    id="${row%%"$US"*}"; rest="${row#*"$US"}"; from="${rest%%"$US"*}"; body="${rest#*"$US"}"

    tracked=$(sqlite3 "$DB" "SELECT EXISTS(SELECT 1 FROM http_requests WHERE message_id=$id);" 2>/dev/null || echo 0)

    # loop guard: bot<->bot chains are the runaway risk
    if is_served "$from"; then
      if ! bot_hops_allowed; then
        [ "$tracked" = 1 ] && track_status "$id" failed bot_hop_limited </dev/null
        log "BOT-HOP CAP reached ($MAX_BOT_HOPS/${WINDOW}s) — dropping msg $id ($from -> $agent) to stop a loop"
        continue
      fi
      record_bot_hop
    fi

    if ! dispatch_allowed; then
      [ "$tracked" = 1 ] && track_status "$id" failed rate_limited </dev/null
      log "RATE CAP reached ($MAX_DISPATCH/${WINDOW}s) — dropping msg $id ($from -> $agent)"
      continue
    fi
    record_dispatch

    log "dispatch msg $id: $from -> $agent"
    : > "$STATE/inflight.$agent"
    dispatch_async "$agent" "$id" "$from" "$body" "$tracked"
  done
  sleep "$POLL_INTERVAL"
done
