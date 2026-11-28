"""card 1364: Kotlin signatures, package-line edges, and the shared files-counted definition."""
from rco import cli, fixture, imports, metrics
from rco import replay as replay_mod
from rco.affected import is_test
from rco.edges import file_edges, repo_facts
from rco.extract import EXTRACTORS
from rco.extract.kotlin import KotlinExtractor
from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.meter import changed_names, interface_changed, read_set
from rco.model import SourceFile, Tree, walk
from rco.output import Exit
from rco.render import _lines

SAMPLE = '''\
@file:JvmName("Util")
package a.b

import a.c.D as E
import a.*

/** A thing. */
@Serializable
data class Thing<T : Any>(val x: Int, private val y: String = "a${f("}")}") : Base(), I<T> where T : Foo {
    constructor(z: Int) : this(z, "")
    val size: Int get() = 1
    var name: String = "n"
        private set
    private val hidden = 1
    internal fun inner() {}
    fun <R> map(f: (T) -> R): List<R> = items.map(f)
        .filter { it != null }
    suspend fun load(path: String): Result<Unit> {
        val q = """x { """
        return Result.success(Unit)
    }
    companion object {
        const val MAX = 5
        fun of(x: Int) = Thing<Int>(x)
    }
    enum class Mode { A, B(1) { override fun f() {} }, C; fun g() {} }
    interface Cb { fun on(); val p: Int }
}

sealed interface Shape
object Registry { val all = mutableListOf<Int>() }
fun String.shout(): String = uppercase() + "!"
val String.len: Int get() = length
inline fun <reified T> Any.cast(): T = this as T
typealias Handler = (Int) -> Unit
fun interface Runner { fun run() }
private fun hidden() {}
const val VERSION = "1.0"
val lazyVal by lazy { 1 }
fun `backtick name`(a: Int) {}
'''


def _flat(text, path="a/T.kt"):
    nodes = KotlinExtractor().extract(SourceFile(path, "0", text.encode()))
    return {key.split(":", 2)[2]: n for key, n in walk(Tree("m", "", (), tuple(nodes)))}, nodes


def _rendered(text):
    out: list[str] = []
    for n in _flat(text)[1]:
        _lines(n, 0, out)
    return [ln.strip() for ln in out]


def test_kotlin_is_registered_and_claims_kt_files():
    assert "kotlin" in [e.name for e in EXTRACTORS]
    assert KotlinExtractor().claims("a/B.kt") and not KotlinExtractor().claims("a/B.java")
    assert lang_of("src/main/kotlin/a/B.kt") == "kotlin"


def test_scanner_masks_templates_raw_strings_nested_comments_and_backticks():
    s = scan('val a = "x ${f("}")} {"; val r = """y { ${z} """; /* a /* b */ { */ fun `n {b}`() {}\n', "kotlin")
    assert s.code.count("{") == 1 and s.code.count("}") == 1
    assert "`_____`" in s.code and "x " in s.nc


def test_signatures_follow_the_public_contract():
    out = _rendered(SAMPLE)
    text = "\n".join(out)
    assert out[0].startswith("module a/T.kt")
    assert "class Thing<T : Any>(val x: Int, private val y: String = \"a${f(\"}\")}\") : Base(), I<T> where T : Foo" in out
    assert "constructor(z: Int)" in out
    assert "size: Int" in out and "name: String" in out
    assert "map<R>(f: (T) -> R): List<R>" in out
    assert "load(path: String): Result<Unit>" in out
    assert "MAX = 5" in out and "of(x: Int)" in out
    assert "enum Mode" in out and "A" in out and "C" in out and "g()" in out
    assert "interface Cb" in out and "on()" in out and "p: Int" in out
    assert "interface Shape" in out and "object Registry" in out
    assert "String.shout(): String" in out and "String.len: Int" in out
    assert "Any.cast<reified T>(): T" in out
    assert "typealias Handler = (Int) -> Unit" in out
    assert "VERSION = \"1.0\"" in out and "lazyVal" in out and "backtick name(a: Int)" in out
    for hidden in ("hidden", "inner"):
        assert hidden not in text, hidden


def test_package_line_is_recorded_and_file_annotations_are_skipped():
    _f, nodes = _flat(SAMPLE)
    assert dict(nodes[0].attrs)["package"] == "a.b"


def test_expression_bodies_and_accessors_end_at_the_next_declaration():
    out = _rendered("package p\nfun a() = listOf(1)\n    .map { it }\nval b: Int\n    get() = 1\n"
                    "    private set\nfun c(): String { return \"\" }\nvar d = 1\n")
    assert out[1:] == ["a()", "b: Int", "c(): String", "d"]


def test_extracting_is_deterministic_and_never_raises_on_odd_input():
    a, _ = _flat(SAMPLE)
    assert a == _flat(SAMPLE)[0]
    for odd in ("", "class", "class A(", "fun f(", 'val s = "x', "@@@ {{{", "/* open", "fun interface",
                "enum class E { A(", "val (a, b) = 1 to 2", "companion object"):
        _flat(odd)


# ---------------------------------------------------------------- package edges

def _edges(files: dict[str, str]):
    srcs = {p: t.encode() for p, t in files.items()}
    facts = repo_facts(frozenset(files), srcs)
    return {p: file_edges(p, srcs[p], facts) for p in files if p.endswith((".kt", ".java"))}


def test_import_resolves_by_package_line_not_folder():
    g = _edges({
        "m/lib/Anywhere.kt": "package b.util\nclass Strings\nfun shout() {}\n",
        "m/app/Main.kt": "package a\nimport b.util.Strings\nclass Main { val s: Strings? = null }\n",
        "m/app/Fun.kt": "package a\nimport b.util.shout\nfun f() = shout()\n",
    })
    assert g["m/app/Main.kt"] == ("m/lib/Anywhere.kt",)
    assert g["m/app/Fun.kt"] == ("m/lib/Anywhere.kt",)


def test_alias_wildcard_nested_and_comment_imports():
    g = _edges({
        "l/S.kt": "package b\nobject Strings { class Inner }\nclass Tool\nclass Spare\n",
        "a/One.kt": "package a\nimport b.Strings as S\nclass One\n",
        "a/Two.kt": "package a\nimport b.*\nclass Two { val t = Tool() }\n",
        "a/Three.kt": "package a\nimport b.Strings.Inner\nclass Three\n",
        "a/Four.kt": "package a\n// import b.Strings\nclass Four\n",
    })
    assert g["a/One.kt"] == ("l/S.kt",) and g["a/Two.kt"] == ("l/S.kt",)
    assert g["a/Three.kt"] == ("l/S.kt",) and g["a/Four.kt"] == ()


def test_same_package_in_another_source_root_counts_and_other_packages_need_an_import():
    g = _edges({
        "m/src/main/kotlin/a/Calc.kt": "package a\nclass Calc { fun add(x: Int) = x }\n",
        "m/src/test/kotlin/a/CalcTest.kt": "package a\nclass CalcTest { val c = Calc() }\n",
        "m/src/test/kotlin/z/Other.kt": "package z\nclass Other { val c = Calc() }\n",
    })
    assert g["m/src/test/kotlin/a/CalcTest.kt"] == ("m/src/main/kotlin/a/Calc.kt",)
    assert g["m/src/test/kotlin/z/Other.kt"] == ()


def test_kotlin_and_java_see_each_other_and_twins_that_tie_all_stay():
    g = _edges({
        "j/p/Old.java": "package p;\npublic class Old { New n; }\n",
        "k/p/New.kt": "package p\nclass New { val o: Old? = null }\n",
        "k2/q/User.kt": "package q\nimport p.Old\nimport p.New\nclass User\n",
        "c/src/commonMain/p/Dup.kt": "package p\nexpect class Dup\n",
        "c/src/jvmMain/p/Dup.kt": "package p\nactual class Dup\n",
        "x/p/Use.kt": "package p\nclass Use { val d: Dup? = null }\n",
    })
    assert g["j/p/Old.java"] == ("k/p/New.kt",)
    assert g["k/p/New.kt"] == ("j/p/Old.java",)
    assert g["k2/q/User.kt"] == ("j/p/Old.java", "k/p/New.kt")
    assert g["x/p/Use.kt"] == ("c/src/commonMain/p/Dup.kt", "c/src/jvmMain/p/Dup.kt")


def test_file_graph_includes_kotlin_and_ignores_it_when_absent():
    files = {"s/p/A.kt": b"package p\nclass A\n", "s/q/B.kt": b"package q\nimport p.A\nclass B\n"}
    assert imports.file_graph(sorted(files), files)["s/q/B.kt"] == ("s/p/A.kt",)


# ---------------------------------------------------- files counted (card 1359)

LIB_V1 = "package p\nclass A {\n  fun calc(a: Int): Int = a\n}\n"
LIB_SIG = LIB_V1.replace("calc(a: Int)", "calc(a: Int, b: Int)")
LIB_BODY = LIB_V1.replace("= a", "= a + 0")
USE = "package q\nimport p.A\nclass B { fun g() = A().calc(1) }\n"


def test_a_signature_change_is_a_contract_change_and_a_body_change_is_not():
    p = "s/p/A.kt"
    assert interface_changed(p, LIB_V1.encode(), LIB_SIG.encode())
    assert not interface_changed(p, LIB_V1.encode(), LIB_BODY.encode())
    assert "calc" in changed_names(p, LIB_V1.encode(), LIB_SIG.encode())


def test_read_set_adds_kotlin_importers_only_when_the_signature_changed():
    files = {"s/p/A.kt": LIB_SIG.encode(), "s/q/B.kt": USE.encode()}
    graph = imports.file_graph(sorted(files), files)
    _c, _i, importers = read_set(["s/p/A.kt"], graph, ["s/p/A.kt"])
    assert importers == ["s/q/B.kt"]
    _c, _i, importers = read_set(["s/p/A.kt"], graph, [])
    assert importers == []


def test_measure_and_replay_agree_on_a_kotlin_signature_change(tmp_path):
    repo, _base = fixture.make_repo(tmp_path / "r", {"s/p/A.kt": LIB_V1, "s/q/B.kt": USE,
                                                     "s/q/Other.kt": "package q\nclass Other\n"})
    fixture.commit(repo, {"s/p/A.kt": LIB_SIG})
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    parent = fixture.git(repo, "rev-parse", "HEAD^").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "s/p/A.kt",
                     "--out", str(pack)]) == Exit.OK
    name, count = metrics.counter()
    doc = metrics.write_metrics(repo, parent, head, 1, pack.read_text(), 12000, count, name)
    one = replay_mod.replay(repo, last=1, budget=12000, max_files=200, count=count,
                            tokenizer=name).to_obj()["commits"][0]
    assert doc["without_rco"]["tokens"] == one["without"]["tokens"]
    assert doc["without_rco"]["importer_files"] == one["without"]["importer_files"] == 1
    assert one["coverage"]["call_sites"] >= 1


def test_kotlin_test_files_are_tests_for_affected():
    for p in ("m/src/test/kotlin/a/Thing.kt", "a/ThingTest.kt", "a/ThingTests.kt", "m/src/jvmTest/kotlin/a/B.kt"):
        assert is_test(p), p
    assert not is_test("m/src/main/kotlin/a/Thing.kt")


def test_a_java_file_imports_a_kotlin_class_from_another_package_by_name_and_by_star():
    g = _edges({
        "k/b/Greeter.kt": "package b\nclass Greeter { fun hi() {} }\n",
        "k/b/Other.kt": "package b\nclass Other\n",
        "j/a/ByName.java": "package a;\nimport b.Greeter;\nclass ByName { Greeter g; Other o; }\n",
        "j/a/ByStar.java": "package a;\nimport b.*;\nclass ByStar { Other o; }\n",
        "j/a/Nothing.java": "package a;\nclass Nothing { Greeter g; }\n",
    })
    assert g["j/a/ByName.java"] == ("k/b/Greeter.kt",)  # Other is not imported, so not seen
    assert g["j/a/ByStar.java"] == ("k/b/Other.kt",)
    assert g["j/a/Nothing.java"] == ()


def test_java_sees_top_level_functions_through_the_file_facade():
    g = _edges({
        "k/b/Fac.kt": "package b\nfun twice(x: Int) = x * 2\n",
        "k/b/Named.kt": '@file:JvmName("Util")\npackage b\nfun thrice(x: Int) = x * 3\n',
        "k/b/OnlyClass.kt": "package b\nclass OnlyClass\n",
        "j/a/Use.java": "package a;\nimport b.FacKt;\nimport b.Util;\nclass Use { int n = FacKt.twice(1) + Util.thrice(1); }\n",
        "j/a/NoFacade.java": "package a;\nimport b.OnlyClassKt;\nclass NoFacade {}\n",
        "k/b/Same.java": "package b;\nclass Same { int n = FacKt.twice(1); }\n",
    })
    assert g["j/a/Use.java"] == ("k/b/Fac.kt", "k/b/Named.kt")
    assert g["j/a/NoFacade.java"] == ()
    assert g["k/b/Same.java"] == ("k/b/Fac.kt",)
