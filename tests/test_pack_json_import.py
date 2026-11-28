"""Card 1349: pack --for a TS file that imports an in-repo JSON file must not crash."""
from rco import cli, fixture
from rco.output import Exit
from rco.pack import pack, pack_markdown


def test_ts_import_of_package_json_does_not_crash(tmp_path, capsys):
    repo, _sha = fixture.make_repo(tmp_path / "v", {
        "app/main.ts": (
            "import pkg from './package.json'\n\n"
            "export function main() {\n    return pkg.name\n}\n"
        ),
        "app/package.json": '{"name": "app"}\n',
    })
    p = pack(repo, _sha, ["app/main.ts"], 12000)
    text = pack_markdown(p, "app/main.ts")
    assert all(n.path != "app/package.json" for t in p.trees for n in t.nodes)
    assert "app/package.json" not in text
    assert cli.main(["pack", "--repo", str(repo), "--for", "app/main.ts"]) == Exit.OK
