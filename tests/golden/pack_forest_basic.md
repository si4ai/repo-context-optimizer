# rco pack  (SHA=46ac956, budget=8000 estimated tokens)

## Forest
- .: layer 0
- db: layer 0, 1 tables
- src/app: layer 2, 1 commands (depends on: src/app/core)
- src/app/core: layer 1 (depends on: src/app/util) (used by: src/app)
- src/app/cyc_a: layer 0 (depends on: src/app/cyc_b) (used by: src/app/cyc_b)
- src/app/cyc_b: layer 0 (depends on: src/app/cyc_a) (used by: src/app/cyc_a)
- src/app/util: layer 0 (used by: src/app/core)

## Trees (full, for --for src/app/cli.py)
### src/app
module app
module app.__main__
module app.cli
  def main() -> int
CLI: app
  --fast action=store_true

## One hop (name and one line)
- src/app/core: layer 1

# budget used: ~174 / 8000 estimated tokens
