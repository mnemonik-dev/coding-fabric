#!/usr/bin/env bash
# Sample `docker stats` of the Groth16 wrap container while `prove` runs (D20).
# Usage: wrap-sampler.sh RUN_DIR [INTERVAL_SECONDS]
# Appends "<UTC ts> <name> <image> <mem usage> <cpu%>" lines to RUN_DIR/wrap-stats.log
# until killed. The wrap runs from a risczero groth16/stark2snark image.
set -u
run_dir="$1"
interval="${2:-2}"
log="$run_dir/wrap-stats.log"
: >> "$log"
while true; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker ps --format '{{.ID}} {{.Names}} {{.Image}}' 2>/dev/null \
    | grep -Ei 'groth16|stark2snark|risc0' \
    | while read -r id name image; do
        stats=$(docker stats --no-stream --format '{{.MemUsage}} {{.CPUPerc}}' "$id" 2>/dev/null) || continue
        echo "$ts $name $image $stats" >> "$log"
      done
  sleep "$interval"
done
