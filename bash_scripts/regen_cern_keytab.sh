#!/bin/bash
# Regenerate the CERN Kerberos keytab used for unattended (reboot-safe) kinit by
# beam_watcher and backup_watcher. Run this ONCE, and again any time the CERN
# account password changes (that is what silently killed the old keytab).
#
# Why this script exists / the gotcha it encodes:
#   The principal dneff@CERN.CH is a UPN alias. The AD account's key salt is
#   derived from the sAMAccountName "dylan.neff", so the real string2key salt is
#   "CERN.CHdylan.neff" -- NOT ktutil's default "CERN.CHdneff". A keytab built
#   with the default salt authenticates with the wrong key and fails preauth.
#   We pass the correct salt explicitly with `addent -s`.
#   (To rediscover the salt if the account ever changes:
#      KRB5_TRACE=/dev/stdout kinit dneff@CERN.CH   and grep for 'salt'.)
#
# ktutil reads the password from stdin, so we pipe it in -- the user types it
# once, it never touches argv or disk.

set -euo pipefail

PRINC="dneff@CERN.CH"
SALT="CERN.CHdylan.neff"
KEYTAB="${HOME}/.keytab/mx17_cern.keytab"
ENCTYPES=("aes256-cts-hmac-sha1-96" "aes128-cts-hmac-sha1-96")

echo "Regenerating keytab for ${PRINC}"
echo "  salt   : ${SALT}"
echo "  keytab : ${KEYTAB}"
echo "  enctypes: ${ENCTYPES[*]}"
echo

read -rsp "CERN password for ${PRINC}: " PW
echo

mkdir -p "$(dirname "$KEYTAB")"
rm -f "$KEYTAB"          # start clean so no stale-KVNO entries accumulate

# Build the ktutil command stream: for each enctype an addent line immediately
# followed by the password line (ktutil prompts per addent), then write + quit.
{
  for enc in "${ENCTYPES[@]}"; do
    printf '%s\n' "addent -password -p ${PRINC} -k 1 -e ${enc} -s ${SALT}"
    printf '%s\n' "$PW"
  done
  printf '%s\n' "wkt ${KEYTAB}"
  printf '%s\n' "quit"
} | ktutil >/dev/null

PW=""                    # drop the password from memory ASAP
chmod 600 "$KEYTAB"

echo "Keytab written. Entries:"
klist -kt "$KEYTAB"

echo
echo "Verifying it can obtain a ticket (scratch cache, live ticket untouched)..."
SCRATCH="/tmp/kt_verify_$$"
if kinit -kt "$KEYTAB" -c "$SCRATCH" "$PRINC" 2>&1; then
    echo "OK -- keytab authenticates against the KDC."
    klist -c "$SCRATCH" | sed -n '1,6p'
    rm -f "$SCRATCH"
    echo
    echo "SUCCESS. beam_watcher / backup_watcher / cron can now kinit unattended."
else
    rm -f "$SCRATCH"
    echo "FAILED -- keytab did not authenticate. Password wrong, or the salt changed."
    echo "Re-check the salt with: KRB5_TRACE=/dev/stdout kinit ${PRINC}  (grep salt)"
    exit 1
fi
