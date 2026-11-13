# rco pack  (SHA=46ac956, budget=8000 estimated tokens)

## Forest
- .: layer 0
- db: layer 0; Owns table users.
- src/app: layer 2; Defines function main. Owns command app. (depends on: src/app/core)
- src/app/core: layer 1; Defines function run. (depends on: src/app/util) (used by: src/app)
- src/app/cyc_a: layer 0; Defines constant A. (depends on: src/app/cyc_b) (used by: src/app/cyc_b)
- src/app/cyc_b: layer 0; Defines constant B. (depends on: src/app/cyc_a) (used by: src/app/cyc_a)
- src/app/util: layer 0; Defines function clamp. (used by: src/app/core)

## Trees (full, for --for src/app/cli.py)
### src/app
module app
module app.__main__
module app.cli
  def main() -> int  # Main. Returns int. Calls engine.py:run.
CLI: app
  --fast action=store_true

## One hop (name and one line)
- src/app/core: layer 1; Defines function run.

# budget used: ~223 / 8000 estimated tokens
