# Disk-space diagnosis and cleanup

```bash
# Current free space.
df -h /System/Volumes/Data

# Largest top-level user directories.
gdu -x -d 1 -h "$HOME/src" 2>/dev/null | sort -h
gdu -x -d 1 -h "$HOME/Library" 2>/dev/null | sort -h

# Application data and rebuildable caches.
gdu -x -d 1 -h "$HOME/Library/Application Support" 2>/dev/null | sort -h
gdu -x -d 1 -h "$HOME/Library/Caches" 2>/dev/null | sort -h

# Docker storage and safe Docker cleanup.
docker system df
docker builder prune --all --force
docker image prune --all --force

# Locate Rust build-output directories.
find "$HOME/src" -type d -name target -prune -print

# Inspect a specific Rust project's build output.
gdu -x -d 2 -h "$HOME/src/sessions/genetic_algorithms/neutrino/target" 2>/dev/null | sort -h

# Clean one Rust project; removes only generated Cargo build artifacts.
(cd "$HOME/src/sessions/genetic_algorithms/neutrino" && cargo clean)

# Clean all remaining Cargo projects that currently have a top-level target/ directory.
# Review the preceding `find` output before running this command.
find "$HOME/src" -type d -name target -prune -print0 |
  while IFS= read -r -d '' target_dir; do
    project_dir="${target_dir%/target}"
    if [[ -f "$project_dir/Cargo.toml" ]]; then
      (cd "$project_dir" && cargo clean)
    fi
  done

# Disable future incremental Rust build caches in a project Cargo.toml.
# [profile.dev]
# incremental = false
```
