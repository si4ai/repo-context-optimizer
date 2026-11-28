# rco pack  (SHA=46ac956, budget=12000 estimated tokens)
# built from 46ac956 (git); HEAD 46ac956; current; working tree matches

## Forest
- src/app: layer 2; Defines function main. Owns command app. (depends on: src/app/core)
- src/app/core: layer 1; Defines function run. (depends on: src/app/util) (used by: src/app)

## Trees (files imported by --for src/app/cli.py; the --for files are not outlined)
### src/app/core
module app.core.engine
  def run() -> int  # Run. Returns int. Calls helpers.py:clamp. Called by cli.py:main.

## One hop (name and one line)
- src/app/core: layer 1; Defines function run.

# budget used: ~191 / 12000 estimated tokens
