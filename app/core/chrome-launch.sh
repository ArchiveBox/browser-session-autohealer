#!/bin/sh
# Chrome outlives the launch driver; its output must not point at that driver's pipes.
if [ -n "${ACCOUNT_CHECKER_CPU_GUARD:-}" ]; then
    test -r "$ACCOUNT_CHECKER_CPU_GUARD" || exit 1
    export LD_PRELOAD="$ACCOUNT_CHECKER_CPU_GUARD${LD_PRELOAD:+:$LD_PRELOAD}"
fi
exec "$ACCOUNT_CHECKER_CHROME_BINARY" "$@" >>"$ACCOUNT_CHECKER_CHROME_LOG" 2>&1
