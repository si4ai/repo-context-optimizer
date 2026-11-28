"""card 1362: Java signatures, import edges, and the shared files-counted definition."""
from rco import cli, fixture, imports, metrics
from rco import replay as replay_mod
from rco.affected import is_test
from rco.edges import file_edges, repo_facts
from rco.extract import EXTRACTORS
from rco.extract.java import JavaExtractor
from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.meter import changed_names, interface_changed, public_contract, read_set
from rco.model import Kind, SourceFile, Tree, walk
from rco.output import Exit
from rco.render import _lines

SAMPLE = '''\
package a.b;

import static x.Y.z;

/** Doc line. */
@Deprecated
public final class K<T> implements Runnable {
  public static final int MAX = 5, MIN = -1;
  public static final String S = "hi // not a comment";
  protected int count;
  private int hidden;
  int pkg;
  @Nullable public String[] names, other;
  public int a[], b;
  public K() {}
  /** Get one. */
  protected <U> U get(@Nullable final U u, int... xs) throws E { return u; }
  public void over(int x) {}
  public void over(String x) {}
  void pkgm() {}
  public enum Color { RED, GREEN("g") { void f() {} }; public int code() { return 1; } }
  public interface Cb { void on(); default int d() { return 1; } int LIM = 3; }
  public record P(int x, String y) implements Q { public P { } public int sum() { return x; } }
  public @interface Tagged { String v() default "a"; }
  static { int q = 1; }
  public String block = """
      public void notAMethod() { }
      """;
  public abstract static class Inner { public abstract void run(); }
}
class Hidden { public void x() {} }
'''


def _flat(text, path="a/b/K.java"):
    nodes = JavaExtractor().extract(SourceFile(path, "0", text.encode()))
    return {key.split(":", 2)[2]: n for key, n in walk(Tree("m", "", (), tuple(nodes)))}, nodes


def test_java_is_registered_and_claims_java_files():
    assert "java" in [e.name for e in EXTRACTORS]
    assert JavaExtractor().claims("a/B.java") and not JavaExtractor().claims("a/B.kt")
    assert lang_of("src/main/java/a/B.java") == "java"


def test_scanner_masks_comments_strings_chars_and_text_blocks():
    s = scan('class A { String s = "a // b"; char c = \'"\'; String t = """\n x { y \\""" z\n """; '
             '/* { */ int n; }\n', "java")
    assert s.code.count("{") == 1 and s.code.count("}") == 1
    assert s.depth_at(len(s.code) - 2) == 1
    assert "a // b" in s.nc and "a // b" not in s.code


def test_signatures_follow_the_public_contract():
    flat, nodes = _flat(SAMPLE)
    top = nodes[0]
    assert top.kind is Kind.SRCMODULE
    assert dict(top.attrs) == {"lang": "java", "package": "a.b"}
    k = flat["K"]
    assert k.sig == "<T> implements Runnable" and k.doc == "Doc line."
    assert dict(k.attrs) == {"kind": "class", "final": "1"}
    assert flat["K.MAX"].kind is Kind.CONSTANT and dict(flat["K.MAX"].attrs)["value"] == "5"
    assert dict(flat["K.MIN"].attrs)["value"] == "-1"
    assert dict(flat["K.S"].attrs)["value"] == '"hi // not a comment"'
    assert flat["K.count"].kind is Kind.FIELD and flat["K.count"].sig == "int"
    assert flat["K.names"].sig == "String[]" and flat["K.a"].sig == "int[]" and flat["K.b"].sig == "int"
    assert not {"K.hidden", "K.pkg", "K.pkgm", "Hidden"} & set(flat)
    assert flat["K.constructor"].kind is Kind.METHOD
    assert flat["K.get"].sig == "<U>(U u, int... xs): U throws E"
    assert flat["K.get"].doc == "Get one."
    assert {"K.Color", "K.Color.RED", "K.Color.GREEN", "K.Color.code"} <= set(flat)
    assert "K.Color.f" not in flat
    assert flat["K.Cb.on"].sig == "(): void" and dict(flat["K.Cb.d"].attrs) == {"default": "1"}
    assert flat["K.Cb.LIM"].kind is Kind.CONSTANT  # interface fields are constants
    assert flat["K.P"].sig == "(int x, String y) implements Q"
    assert "K.P.sum" in flat and "K.q" not in flat
    assert flat["K.Tagged.v"].sig == '(): String default "a"'
    assert dict(flat["K.Inner"].attrs) == {"abstract": "1", "kind": "class", "static": "1"}
    assert "K.notAMethod" not in flat and flat["K.block"].kind is Kind.FIELD


def test_overloads_keep_one_node_each_and_render_without_a_keyword():
    _flat_, nodes = _flat(SAMPLE)
    out: list[str] = []
    for n in nodes:
        _lines(n, 0, out)
    over = [ln.strip() for ln in out if ln.strip().startswith("over(")]
    assert over == ["over(int x): void", "over(String x): void"]


def test_extracting_is_deterministic_and_never_raises_on_odd_input():
    a, _ = _flat(SAMPLE)
    b, _ = _flat(SAMPLE)
    assert a == b
    for odd in ("", "public class", "class A { void f( ", "public class A { String s = \"x", "@@@ {{{",
                "public class A { /* open"):
        _flat(odd)


# ---------------------------------------------------------------- import edges

def _edges(files: dict[str, str]):
    srcs = {p: t.encode() for p, t in files.items()}
    facts = repo_facts(frozenset(files), srcs)
    return {p: file_edges(p, srcs[p], facts) for p in files if p.endswith(".java")}


def test_import_resolves_under_a_maven_source_root():
    g = _edges({
        "m/src/main/java/com/acme/util/Strings.java": "package com.acme.util;\npublic class Strings {}\n",
        "m/src/main/java/com/acme/app/Main.java": (
            "package com.acme.app;\nimport com.acme.util.Strings;\nimport java.util.List;\n"
            "public class Main { Strings s; }\n"),
    })
    assert g["m/src/main/java/com/acme/app/Main.java"] == ("m/src/main/java/com/acme/util/Strings.java",)
    assert g["m/src/main/java/com/acme/util/Strings.java"] == ()


def test_import_works_with_any_source_root_not_just_maven():
    g = _edges({"src/p/A.java": "package p;\npublic class A {}\n",
                "src/q/B.java": "package q;\nimport p.A;\nclass B {}\n"})
    assert g["src/q/B.java"] == ("src/p/A.java",)


def test_the_importers_own_root_wins_over_a_twin_in_another_root():
    files = {"android/lib/src/p/A.java": "package p;\npublic class A {}\n",
             "lib/src/p/A.java": "package p;\npublic class A {}\n",
             "lib/src/q/B.java": "package q;\nimport p.A;\nclass B {}\n",
             "android/lib/src/q/B.java": "package q;\nimport p.A;\nclass B {}\n",
             "lib-tests/test/q/BTest.java": "package q;\nimport p.A;\nclass BTest {}\n"}
    g = _edges(files)
    assert g["lib/src/q/B.java"] == ("lib/src/p/A.java",)
    assert g["android/lib/src/q/B.java"] == ("android/lib/src/p/A.java",)
    assert g["lib-tests/test/q/BTest.java"] == ("lib/src/p/A.java",)  # nearest name, not first sorted


def test_static_nested_and_wildcard_imports():
    g = _edges({
        "s/p/A.java": "package p;\npublic class A { public static int f() { return 1; } public static class In {} }\n",
        "s/p/B.java": "package p;\npublic class B {}\n",
        "s/p/C.java": "package p;\npublic class C {}\n",
        "s/q/U1.java": "package q;\nimport static p.A.f;\nclass U1 {}\n",
        "s/q/U2.java": "package q;\nimport p.A.In;\nclass U2 {}\n",
        "s/q/U3.java": "package q;\nimport p.*;\nclass U3 { B b; }\n",
        "s/q/U4.java": "package q;\nimport p.A.*;\nclass U4 {}\n",
    })
    assert g["s/q/U1.java"] == ("s/p/A.java",)
    assert g["s/q/U2.java"] == ("s/p/A.java",)
    assert g["s/q/U3.java"] == ("s/p/B.java",)  # only the class of p the file names
    assert g["s/q/U4.java"] == ("s/p/A.java",)


def test_same_package_use_counts_without_an_import_and_comments_do_not():
    g = _edges({
        "s/p/A.java": "package p;\nclass A {}\n",
        "s/p/B.java": "package p;\nclass B { A a; }\n",
        "s/p/C.java": "package p;\n// A\n/* A */\nclass C { String s = \"A\"; }\n",
        "s/q/D.java": "package q;\n// import p.A;\nclass D {}\n",
    })
    assert g["s/p/B.java"] == ("s/p/A.java",)
    assert g["s/p/C.java"] == () and g["s/q/D.java"] == ()


def test_same_package_under_another_source_root_counts_too():
    g = _edges({
        "m/src/main/java/com/acme/app/Main.java": "package com.acme.app;\npublic class Main {}\n",
        "m/src/main/java/com/acme/app/SideAid.java": "package com.acme.app;\nclass SideAid {}\n",
        "m/src/test/java/com/acme/app/MainTest.java":
            "package com.acme.app;\nclass MainTest { void t() { Main.main(); } }\n",
        "m/src/test/java/com/acme/other/StrayOne.java": "package com.acme.other;\nclass StrayOne { Main m; }\n",
    })
    assert g["m/src/test/java/com/acme/app/MainTest.java"] == ("m/src/main/java/com/acme/app/Main.java",)
    assert g["m/src/test/java/com/acme/other/StrayOne.java"] == ()  # another package: needs an import


def test_a_twin_root_picks_its_own_copy_for_a_same_package_class_in_another_root():
    g = _edges({
        "guava/src/p/Strings.java": "package p;\npublic class Strings {}\n",
        "android/guava/src/p/Strings.java": "package p;\npublic class Strings {}\n",
        "guava-tests/test/p/StringsTest.java": "package p;\nclass StringsTest { Strings a; }\n",
        "android/guava-tests/test/p/StringsTest.java": "package p;\nclass StringsTest { Strings a; }\n",
    })
    assert g["guava-tests/test/p/StringsTest.java"] == ("guava/src/p/Strings.java",)
    assert g["android/guava-tests/test/p/StringsTest.java"] == ("android/guava/src/p/Strings.java",)


def test_an_explicit_import_shadows_the_same_package_name():
    g = _edges({"s/p/List.java": "package p;\nclass List {}\n",
                "s/o/List.java": "package o;\npublic class List {}\n",
                "s/p/U.java": "package p;\nimport o.List;\nclass U { List l; }\n"})
    assert g["s/p/U.java"] == ("s/o/List.java",)


def test_file_graph_includes_java_and_ignores_it_when_absent():
    files = {"s/p/A.java": b"package p;\npublic class A {}\n",
             "s/q/B.java": b"package q;\nimport p.A;\nclass B {}\n"}
    assert imports.file_graph(sorted(files), files)["s/q/B.java"] == ("s/p/A.java",)


# ---------------------------------------------------- files counted (card 1359)

LIB_V1 = "package p;\npublic class A {\n  public int calc(int a) { return a; }\n  public int calc(String s) { return 1; }\n}\n"
LIB_SIG = LIB_V1.replace("calc(int a)", "calc(int a, int b)")
LIB_BODY = LIB_V1.replace("return a;", "return a + 0;")
USE = "package q;\nimport p.A;\npublic class B { int g() { return new A().calc(1); } }\n"


def test_an_overload_signature_change_is_a_contract_change_and_a_body_change_is_not():
    p = "s/p/A.java"
    assert interface_changed(p, LIB_V1.encode(), LIB_SIG.encode())
    assert not interface_changed(p, LIB_V1.encode(), LIB_BODY.encode())
    assert "calc" in changed_names(p, LIB_V1.encode(), LIB_SIG.encode())
    # the changed overload is not the last one: it must still be noticed
    first_of_two = LIB_V1.replace("calc(String s)", "calc(Object s)")
    assert "calc" in changed_names(p, LIB_V1.encode(), first_of_two.encode())
    assert public_contract(p, LIB_V1.encode())


def test_read_set_adds_java_importers_only_when_the_signature_changed():
    files = {"s/p/A.java": LIB_SIG.encode(), "s/q/B.java": USE.encode()}
    graph = imports.file_graph(sorted(files), files)
    _c, _i, importers = read_set(["s/p/A.java"], graph, ["s/p/A.java"])
    assert importers == ["s/q/B.java"]
    _c, _i, importers = read_set(["s/p/A.java"], graph, [])
    assert importers == []


def test_measure_and_replay_agree_on_a_java_signature_change(tmp_path):
    repo, _base = fixture.make_repo(tmp_path / "r", {"s/p/A.java": LIB_V1, "s/q/B.java": USE,
                                                     "s/q/Other.java": "package q;\npublic class Other {}\n"})
    fixture.commit(repo, {"s/p/A.java": LIB_SIG})
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    parent = fixture.git(repo, "rev-parse", "HEAD^").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "s/p/A.java",
                     "--out", str(pack)]) == Exit.OK
    name, count = metrics.counter()
    doc = metrics.write_metrics(repo, parent, head, 1, pack.read_text(), 12000, count, name)
    one = replay_mod.replay(repo, last=1, budget=12000, max_files=200, count=count,
                            tokenizer=name).to_obj()["commits"][0]
    assert doc["without_rco"]["tokens"] == one["without"]["tokens"]
    assert doc["without_rco"]["importer_files"] == one["without"]["importer_files"] == 1
    assert one["coverage"]["call_sites"] >= 1


def test_java_test_files_are_tests_for_affected():
    for p in ("m/src/test/java/p/AThing.java", "p/AThingTest.java", "p/ATests.java", "p/AIT.java",
              "guava-tests/test/p/A.java"):
        assert is_test(p), p
    assert not is_test("m/src/main/java/p/A.java")
