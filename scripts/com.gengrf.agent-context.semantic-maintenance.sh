#!/bin/sh
set -eu
mkdir -p /Users/gengrf/agent-context-system/logs
export AGENT_CONTEXT_ROOT=/Users/gengrf/agent-context-system
/Users/gengrf/agent-context-system/agent-context semantic-maintain --out /Users/gengrf/agent-context-system --source all --budget 32 --max-jobs 2 --min-interval-minutes 30
/Users/gengrf/agent-context-system/agent-context semantic-ann-prune --out /Users/gengrf/agent-context-system --max-entries 32 --max-bytes 1000000000
/Users/gengrf/agent-context-system/agent-context semantic-launchd-monitor --out /Users/gengrf/agent-context-system --label com.gengrf.agent-context.semantic-maintenance --tail-lines 40 --with-launchctl
