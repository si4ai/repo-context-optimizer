"""card 1365: PHP signatures, composer psr-4 edges, and the shared files-counted definition."""
import json

from rco import cli, fixture, imports, metrics
from rco import replay as replay_mod
from rco.affected import is_test
from rco.edges import file_edges, repo_facts
from rco.extract import EXTRACTORS
from rco.extract.langs import lang_of
from rco.extract.php import PhpExtractor
from rco.extract.scan import scan
from rco.meter import changed_names, interface_changed, read_set
from rco.model import SourceFile
from rco.output import Exit
from rco.render import _lines

SAMPLE = '''\
<?php

declare(strict_types=1);

namespace App\\Models;

use Illuminate\\Support\\Collection;
use function Illuminate\\Support\\collect;

/**
 * A user.
 */
#[Attr(1)]
abstract class User extends Model implements Jsonable, \\Countable
{
    use HasFactory, Notifiable { Notifiable::boot as protected bootNotifiable; }

    public const ROLE = 'admin', OTHER = 2;
    private const HIDDEN = 1;
    public static ?int $count = 0;
    protected array $items = [], $more;
    private $secret;
    var $legacy;

    public function __construct(private readonly string $name, protected int $age = 3)
    {
        $this->x = "a {$b["k"]} {";
    }

    /** Get items. */
    public function items(?array $filter = null): Collection|array
    {
        return collect($this->items);
    }

    abstract protected function make(int ...$ids): static;
    private function hidden(): void {}
    function implicit(): void {}
    public static function &ref(): array { static $a = []; return $a; }
}

interface Jsonable { public function toJson(int $options = 0): string; const V = 1; }
trait Notifiable { public function notify(): void {} }
enum Status: string implements HasLabel {
    case Active = 'active';
    case Off = 'off';
    public function label(): string { return ucfirst($this->value); }
}

if (! function_exists('abort')) {
    function abort($code, string $message = ''): never { throw new X; }
}
function helper(int $a): int { return $a; }
const TOP = 5;
$x = new class extends Foo { public function anon() {} };
'''


def _rendered(text, path="a/T.php"):
    out: list[str] = []
    for n in PhpExtractor().extract(SourceFile(path, "0", text.encode())):
        _lines(n, 0, out)
    return [ln.strip() for ln in out]


def test_php_is_registered_and_claims_php_files():
    assert "php" in [e.name for e in EXTRACTORS]
    assert PhpExtractor().claims("a/B.php") and not PhpExtractor().claims("a/B.py")
    assert lang_of("src/Illuminate/Support/Str.php") == "php"


def test_scanner_reads_only_php_and_masks_comments_strings_and_heredocs():
    s = scan('<html>{ <?php\n# c {\n#[Attr(1)]\n$s = "a {$b["}"]} {"; $t = \'x { \\\' }\'; $h = <<<EOT\n { ${x}\n'
             'EOT;\nfunction f() {} // {\n?> <b>{</b> <?= $x ?>\n', "php")
    assert s.code.count("{") == 1 and s.code.count("}") == 1
    assert "#[Attr(1)]" in s.code and "<html>" not in s.code and "<b>" not in s.code


def test_signatures_follow_the_public_contract():
    out = _rendered(SAMPLE)
    text = "\n".join(out)
    assert out[0].startswith("module a/T.php")
    assert "class User extends Model implements Jsonable, \\Countable" in out
    assert "ROLE = 'admin'" in out and "OTHER = 2" in out
    assert "count: ?int" in out and "items: array" in out and "more: array" in out and "legacy" in out
    assert "__construct(private readonly string $name, protected int $age = 3)" in out
    assert "items(?array $filter = null): Collection|array" in out
    assert "make(int ...$ids): static" in out and "implicit(): void" in out and "ref(): array" in out
    assert "interface Jsonable" in out and "toJson(int $options = 0): string" in out and "V = 1" in out
    assert "trait Notifiable" in out and "enum Status : string implements HasLabel" in out
    assert "Active" in out and "Off" in out and "label(): string" in out
    assert "abort($code, string $message = \'\'): never" in out  # inside a function_exists guard
    assert "helper(int $a): int" in out and "TOP = 5" in out
    for hidden in ("HIDDEN", "secret", "hidden", "anon"):
        assert hidden not in text, hidden


def test_namespace_is_recorded():
    n = PhpExtractor().extract(SourceFile("a/T.php", "0", b"<?php\nnamespace A\\B;\nclass K {}\n"))[0]
    assert dict(n.attrs)["namespace"] == "A\\B"


def test_extracting_is_deterministic_and_never_raises_on_odd_input():
    assert _rendered(SAMPLE) == _rendered(SAMPLE)
    for odd in ("", "<?php", "<?php class", "<?php function f(", "<?php $s = \"x", "<?php /* open",
                "<?php enum E { case", "<?php namespace", "just text, no php tag", "<?php if (", "<?php #["):
        _rendered(odd)


# ---------------------------------------------------------------- composer edges

COMPOSER = json.dumps({"autoload": {"psr-4": {"App\\": "app/", "Lib\\Sub\\": ["a/", "b/"]}},
                       "autoload-dev": {"psr-4": {"Tests\\": "tests/"}}})


def _edges(files: dict[str, str]):
    srcs = {p: t.encode() for p, t in files.items()}
    facts = repo_facts(frozenset(files), srcs)
    return {p: file_edges(p, srcs[p], facts) for p in files if p.endswith(".php")}, facts


def test_use_maps_through_the_longest_psr4_prefix_and_group_and_alias_uses():
    g, facts = _edges({
        "composer.json": COMPOSER,
        "app/Models/User.php": "<?php\nnamespace App\\Models;\nuse App\\Support\\{Str, Arr as A};\n"
                               "use Lib\\Sub\\Thing;\nclass User { function f() { return Str::x() . A::y(); } }\n",
        "app/Support/Str.php": "<?php\nnamespace App\\Support;\nclass Str {}\n",
        "app/Support/Arr.php": "<?php\nnamespace App\\Support;\nclass Arr {}\n",
        "app/Support/Unused.php": "<?php\nnamespace App\\Support;\nclass Unused {}\n",
        "b/Thing.php": "<?php\nnamespace Lib\\Sub;\nclass Thing {}\n",
    })
    assert g["app/Models/User.php"] == ("app/Support/Arr.php", "app/Support/Str.php", "b/Thing.php")
    assert ("App\\", "app") in facts.php_psr4 and ("Tests\\", "tests") in facts.php_psr4


def test_a_subpackage_composer_json_is_relative_to_its_own_folder():
    g, facts = _edges({
        "src/Illuminate/Bus/composer.json": json.dumps({"autoload": {"psr-4": {"Illuminate\\Bus\\": ""}}}),
        "src/Illuminate/Bus/Batch.php": "<?php\nnamespace Illuminate\\Bus;\nclass Batch {}\n",
        "src/Illuminate/Queue/Job.php": "<?php\nnamespace Illuminate\\Queue;\nuse Illuminate\\Bus\\Batch;\nclass Job {}\n",
    })
    assert ("Illuminate\\Bus\\", "src/Illuminate/Bus") in facts.php_psr4
    assert g["src/Illuminate/Queue/Job.php"] == ("src/Illuminate/Bus/Batch.php",)


def test_same_namespace_and_fully_qualified_names_need_no_use_and_comments_do_not_count():
    g, _ = _edges({
        "composer.json": COMPOSER,
        "app/Models/Local.php": "<?php\nnamespace App\\Models;\nclass Local {}\n",
        "app/Models/Far.php": "<?php\nnamespace App\\Far;\nclass Far {}\n",
        "app/Models/Main.php": "<?php\nnamespace App\\Models;\nclass Main { Local $l; \\App\\Far\\Far $f; }\n",
        "app/Models/Quiet.php": "<?php\nnamespace App\\Models;\n// Local\n/* \\App\\Far\\Far */\nclass Quiet { }\n",
    })
    assert g["app/Models/Main.php"] == ("app/Models/Local.php", "app/Far/Far.php") or \
        g["app/Models/Main.php"] == ("app/Models/Far.php", "app/Models/Local.php")
    assert g["app/Models/Quiet.php"] == ()


def test_a_class_no_prefix_finds_is_found_by_its_declaration_and_require_paths_are_literal():
    g, _ = _edges({
        "db/Legacy.php": "<?php\nnamespace Old;\nclass Legacy {}\n",
        "app/Use.php": "<?php\nnamespace Mine;\nuse Old\\Legacy;\nrequire __DIR__ . '/boot.php';\n"
                       "include $dynamic;\nclass Use1 {}\n",
        "app/boot.php": "<?php\n",
    })
    assert g["app/Use.php"] == ("app/boot.php", "db/Legacy.php")


def test_file_graph_includes_php_and_ignores_it_when_absent():
    files = {"composer.json": COMPOSER.encode(), "app/A.php": b"<?php\nnamespace App;\nclass A {}\n",
             "app/B.php": b"<?php\nnamespace App\\Sub;\nuse App\\A;\nclass B {}\n"}
    assert imports.file_graph(sorted(files), files)["app/B.php"] == ("app/A.php",)


# ---------------------------------------------------- files counted (card 1359)

LIB_V1 = "<?php\nnamespace P;\nclass A {\n    public function calc(int $a): int { return $a; }\n}\n"
LIB_SIG = LIB_V1.replace("calc(int $a)", "calc(int $a, int $b)")
LIB_BODY = LIB_V1.replace("return $a;", "return $a + 0;")
USE = "<?php\nnamespace Q;\nuse P\\A;\nclass B { function g() { return (new A)->calc(1); } }\n"


def test_a_signature_change_is_a_contract_change_and_a_body_change_is_not():
    p = "s/p/A.php"
    assert interface_changed(p, LIB_V1.encode(), LIB_SIG.encode())
    assert not interface_changed(p, LIB_V1.encode(), LIB_BODY.encode())
    assert "calc" in changed_names(p, LIB_V1.encode(), LIB_SIG.encode())


def test_read_set_adds_php_importers_only_when_the_signature_changed():
    files = {"s/p/A.php": LIB_SIG.encode(), "s/q/B.php": USE.encode()}
    graph = imports.file_graph(sorted(files), files)
    _c, _i, importers = read_set(["s/p/A.php"], graph, ["s/p/A.php"])
    assert importers == ["s/q/B.php"]
    _c, _i, importers = read_set(["s/p/A.php"], graph, [])
    assert importers == []


def test_measure_and_replay_agree_on_a_php_signature_change(tmp_path):
    repo, _base = fixture.make_repo(tmp_path / "r", {"s/p/A.php": LIB_V1, "s/q/B.php": USE,
                                                     "s/q/Other.php": "<?php\nnamespace Q;\nclass Other {}\n"})
    fixture.commit(repo, {"s/p/A.php": LIB_SIG})
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    parent = fixture.git(repo, "rev-parse", "HEAD^").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "s/p/A.php",
                     "--out", str(pack)]) == Exit.OK
    name, count = metrics.counter()
    doc = metrics.write_metrics(repo, parent, head, 1, pack.read_text(), 12000, count, name)
    one = replay_mod.replay(repo, last=1, budget=12000, max_files=200, count=count,
                            tokenizer=name).to_obj()["commits"][0]
    assert doc["without_rco"]["tokens"] == one["without"]["tokens"]
    assert doc["without_rco"]["importer_files"] == one["without"]["importer_files"] == 1
    assert one["coverage"]["call_sites"] >= 1


def test_php_test_files_are_tests_for_affected():
    for p in ("tests/Support/StrTest.php", "src/ThingTest.php", "tests/Foundation/Fixtures/x.php"):
        assert is_test(p), p
    assert not is_test("src/Illuminate/Support/Str.php")



def test_a_line_comment_ends_at_the_php_closer_so_the_next_block_is_read():
    """In PHP, `//` and `#` comments end at `?>` as well as at the newline (D1)."""
    for comment in ("// c", "# c"):
        text = f"<?php\n{comment} ?> html\n<?php\nfunction a1() {{}}\n"
        n = PhpExtractor().extract(SourceFile("a.php", "x", text.encode()))[0]
        assert [c.name for c in n.children] == ["a1"], comment
        s = scan(text, "php")
        assert "function a1()" in s.code and "html" not in s.code
    text = "<?php\n// c ?>\nhtml function c_html() {}\n<?php\nfunction c1() {}\n"
    n = PhpExtractor().extract(SourceFile("c.php", "x", text.encode()))[0]
    assert [c.name for c in n.children] == ["c1"]


def test_use_function_finds_a_function_at_any_indent_in_a_braced_namespace():
    """A function_exists guard at indent 8 is still a `use function` target (D2, W1)."""
    g, _ = _edges({
        "composer.json": COMPOSER,
        "app/Lib.php": "<?php\nnamespace App {\n    if (!function_exists('deep')) {\n"
                       "        function deep() {}\n    }\n    class C { function meth() {} }\n}\n",
        "app/U.php": "<?php\nnamespace App;\nuse function App\\deep;\nclass U { function f() { deep(); } }\n",
    })
    assert g["app/U.php"] == ("app/Lib.php",)
    g2, _ = _edges({
        "composer.json": COMPOSER,
        "app/Lib.php": "<?php\nnamespace App;\nclass C { function meth() {} }\n",
        "app/U.php": "<?php\nnamespace App;\nuse function App\\meth;\nclass U {}\n",
    })
    assert g2["app/U.php"] == ()


def test_the_longest_psr4_prefix_wins_when_two_prefixes_match():
    """Two prefixes can map the same name; the longer one wins (W1)."""
    g, facts = _edges({
        "composer.json": json.dumps({"autoload": {"psr-4": {"App\\": "src/", "App\\Svc\\": "lib/svc/"}}}),
        "src/Svc/Foo.php": "<?php\nnamespace App\\Svc;\nclass Foo {}\n",
        "lib/svc/Foo.php": "<?php\nnamespace App\\Svc;\nclass Foo {}\n",
        "app/Use.php": "<?php\nnamespace App;\nuse App\\Svc\\Foo;\nclass Use1 {}\n",
    })
    assert ("App\\Svc\\", "lib/svc") in facts.php_psr4
    assert g["app/Use.php"] == ("lib/svc/Foo.php",)


def test_composer_lock_and_phpunit_dist_are_build_files():
    from rco.affected import BUILD_FILES, is_infra
    names = BUILD_FILES["php"]
    for n in ("composer.lock", "phpunit.xml.dist"):
        assert n in names, n
        assert is_infra(n) and is_infra(f"pkg/{n}"), n
