<!-- rco:begin (rco init --agents) -->
## rco: read the pack before the code
- Run `rco update` once per checkout.
- Before you read code, run `rco pack --for FILE...` for the files you will touch.
- Read the pack first; open a raw file only when the pack does not answer.
- If the pack's second line does not say `current`, run `rco update` and pack again; read any file on its `changed since this pack` line directly.
- After a compaction, rerun `rco pack` rather than rereading files.
<!-- rco:end -->
