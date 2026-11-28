"""card 1363: C# signatures, namespace and project edges, and the shared files-counted definition."""
from rco import cli, fixture, imports, metrics
from rco import replay as replay_mod
from rco.affected import is_test
from rco.edges import file_edges, repo_facts
from rco.extract import EXTRACTORS
from rco.extract.csharp import CSharpExtractor
from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.meter import changed_names, interface_changed, read_set
from rco.model import SourceFile, Tree, walk
from rco.output import Exit
from rco.render import _lines

SAMPLE = '''\
using System;
namespace Acme.Core
{
    /// <summary>A thing.</summary>
    [Serializable]
    public sealed class Widget<T> : Base, IWidget where T : class, new()
    {
        public const int Max = 5;
        public static readonly string Name = "w // not a comment";
        public int Count { get; private set; }
        public int Double => Count * 2;
        protected internal event EventHandler Changed;
        private protected int sealedOff;
        private int hidden;
        internal int inner;
        public Widget(int count) : base(count) { }
        public (int a, string b) Pair(int x) => (x, "");
        public static IEnumerable<T> Where<T>(IEnumerable<T> src, Func<T, bool> pred) where T : class { yield break; }
        public async Task<Dictionary<string, List<int>>> LoadAsync([NotNull] string path, int[] a = null) { }
        public static Widget<T> operator +(Widget<T> a, Widget<T> b) => a;
        public static implicit operator int(Widget<T> w) => 0;
        public T this[int i] { get { return default; } set { } }
        void IWidget.Explicit() { }
        public void Over(int x) { }
        public void Over(string x) { }
        public string Verb = @"a ""quoted"" } {";
        public enum Mode { A, B = 2, [Obsolete] C }
        public interface ICb { void On(); int Prop { get; } }
        public delegate void Handler<T>(T item, int n);
    }
    public record Person(string First, string Last) : Base;
    internal class Hidden { public void X() { } }
}
'''


def _flat(text, path="a/W.cs"):
    nodes = CSharpExtractor().extract(SourceFile(path, "0", text.encode()))
    return {key.split(":", 2)[2]: n for key, n in walk(Tree("m", "", (), tuple(nodes)))}, nodes


def _rendered(text):
    out: list[str] = []
    for n in _flat(text)[1]:
        _lines(n, 0, out)
    return [ln.strip() for ln in out]


def test_csharp_is_registered_and_claims_cs_files():
    assert "csharp" in [e.name for e in EXTRACTORS]
    assert CSharpExtractor().claims("a/B.cs") and not CSharpExtractor().claims("a/B.java")
    assert lang_of("src/Json/JsonConvert.cs") == "csharp"


def test_scanner_masks_verbatim_raw_strings_comments_and_preprocessor_lines():
    s = scan('class A { string s = @"a ""b"" { "; string t = """\n x { y\n """; /* { */\n#if X\n'
             'int n; }\n', "csharp")
    assert s.code.count("{") == 1 and s.code.count("}") == 1
    assert "#if" not in s.nc and "a " in s.nc


def test_signatures_follow_the_public_contract():
    out = _rendered(SAMPLE)
    text = "\n".join(out)
    assert out[0].startswith("module a/W.cs")
    assert "class Widget<T> : Base, IWidget where T : class, new()" in text
    assert "Max: int = 5" in out and 'Name: string = "w // not a comment"' in out
    assert "Count: int" in out and "Double: int" in out and "Changed: EventHandler" in out
    assert "constructor(int count)" in out
    assert "Pair(int x): (int a, string b)" in out
    assert "Where<T>(IEnumerable<T> src, Func<T, bool> pred): IEnumerable<T> where T : class" in out
    assert "LoadAsync(string path, int[] a = null): Task<Dictionary<string, List<int>>>" in out
    assert "operator +(Widget<T> a, Widget<T> b): Widget<T>" in out
    assert "operator int(Widget<T> w)" in text
    assert "this: [int i]: T" in out
    assert "enum Mode" in out and "A" in out and "C" in out
    assert "interface ICb" in out and "On(): void" in out and "Prop: int" in out
    assert "delegate Handler(T item, int n): void" in out
    assert "record Person(string First, string Last) : Base" in out
    for hidden in ("hidden", "inner", "sealedOff", "Explicit", "Hidden"):
        assert hidden not in text, hidden


def test_namespace_is_recorded_and_file_scoped_namespaces_work():
    _f, nodes = _flat("namespace A.B;\npublic class K { public void M() {} }\n")
    assert dict(nodes[0].attrs)["namespace"] == "A.B"
    assert "M(): void" in _rendered("namespace A.B;\npublic class K { public void M() {} }\n")


def test_overloads_render_one_line_each():
    over = [ln for ln in _rendered(SAMPLE) if ln.startswith("Over(")]
    assert over == ["Over(int x): void", "Over(string x): void"]


def test_extracting_is_deterministic_and_never_raises_on_odd_input():
    a, _ = _flat(SAMPLE)
    assert a == _flat(SAMPLE)[0]
    for odd in ("", "public class", "class A { void f( ", 'public class A { string s = @"x', "[[[ {{{",
                "public class A { /* open", "namespace", "public delegate", "public record R("):
        _flat(odd)


# ---------------------------------------------------------------- namespace edges

def _edges(files: dict[str, str]):
    srcs = {p: t.encode() for p, t in files.items()}
    facts = repo_facts(frozenset(files), srcs)
    return {p: file_edges(p, srcs[p], facts) for p in files if p.endswith(".cs")}


def test_using_maps_a_namespace_to_the_files_declaring_the_types_the_file_names():
    g = _edges({
        "Src/Util/Strings.cs": "namespace Acme.Util { public class Strings {} }\n",
        "Src/Util/Unused.cs": "namespace Acme.Util { public class Unused {} }\n",
        "Src/App/Main.cs": "using Acme.Util;\nnamespace Acme.App { class Main { Strings s; } }\n",
    })
    assert g["Src/App/Main.cs"] == ("Src/Util/Strings.cs",)


def test_own_namespace_and_parent_namespaces_need_no_using_and_comments_do_not_count():
    g = _edges({
        "a/Local.cs": "namespace Acme.App { public class Local {} }\n",
        "a/Parent.cs": "namespace Acme { public class Parent {} }\n",
        "a/Other.cs": "namespace Acme.Other { public class Other {} }\n",
        "a/Main.cs": "namespace Acme.App { class Main { Local l; Parent p; } } // Other\n",
    })
    assert g["a/Main.cs"] == ("a/Local.cs", "a/Parent.cs")


def test_file_scoped_global_static_and_alias_usings():
    g = _edges({
        "l/Strings.cs": "namespace Acme.Util;\npublic static class Strings { public static void F() {} }\n",
        "l/Wrap.cs": "namespace Acme.Wrap;\npublic class Wrap {}\n",
        "g/Globals.cs": "global using Acme.Wrap;\n",
        "a/Main.cs": "using static Acme.Util.Strings;\nnamespace App;\nclass Main { Wrap w; void M() { F(); } }\n",
        "a/Alias.cs": "using S = Acme.Util.Strings;\nnamespace App;\nclass Alias { S s; }\n",
    })
    assert g["a/Main.cs"] == ("l/Strings.cs", "l/Wrap.cs")
    assert g["a/Alias.cs"] == ("l/Strings.cs",)


def test_a_partial_type_links_every_file_in_the_nearest_project():
    g = _edges({
        "P/P.csproj": "<Project/>", "Q/Q.csproj": "<Project/>",
        "P/Big.cs": "namespace N { public partial class Big {} }\n",
        "P/Big.More.cs": "namespace N { public partial class Big {} }\n",
        "Q/Big.cs": "namespace N { public partial class Big {} }\n",
        "P/Use.cs": "namespace N { class Use { Big b; } }\n",
    })
    assert g["P/Use.cs"] == ("P/Big.More.cs", "P/Big.cs")


def test_csproj_project_references_limit_what_a_file_can_see():
    files = {
        "A/A.csproj": '<Project><ItemGroup><ProjectReference Include="..\\B\\B.csproj" /></ItemGroup></Project>',
        "B/B.csproj": '<Project><ItemGroup><ProjectReference Include="../D/D.csproj"/></ItemGroup></Project>',
        "C/C.csproj": "<Project/>", "D/D.csproj": "<Project/>",
        "A/Main.cs": "using Acme;\nnamespace App { class Main { Shared s; Deep d; } }\n",
        "B/Shared.cs": "namespace Acme { public class Shared {} }\n",
        "C/Shared.cs": "namespace Acme { public class Shared {} }\n",
        "D/Deep.cs": "namespace Acme { public class Deep {} }\n",
        "C/Alone.cs": "namespace Acme { class Alone { Shared s; } }\n",
    }
    g = _edges(files)
    assert g["A/Main.cs"] == ("B/Shared.cs", "D/Deep.cs")  # B directly, D through B
    assert g["C/Alone.cs"] == ("C/Shared.cs",)
    facts = repo_facts(frozenset(files), {p: t.encode() for p, t in files.items()})
    assert facts.cs_visible["A"] == frozenset({"A", "B", "D"})


def test_file_graph_includes_csharp_and_ignores_it_when_absent():
    files = {"s/p/A.cs": b"namespace P { public class A {} }\n",
             "s/q/B.cs": b"using P;\nnamespace Q { class B { A a; } }\n"}
    assert imports.file_graph(sorted(files), files)["s/q/B.cs"] == ("s/p/A.cs",)


# ---------------------------------------------------- files counted (card 1359)

LIB_V1 = "namespace P { public class A {\n  public int Calc(int a) { return a; }\n}}\n"
LIB_SIG = LIB_V1.replace("Calc(int a)", "Calc(int a, int b)")
LIB_BODY = LIB_V1.replace("return a;", "return a + 0;")
USE = "using P;\nnamespace Q { public class B { int G() { return new A().Calc(1); } } }\n"


def test_a_signature_change_is_a_contract_change_and_a_body_change_is_not():
    p = "s/p/A.cs"
    assert interface_changed(p, LIB_V1.encode(), LIB_SIG.encode())
    assert not interface_changed(p, LIB_V1.encode(), LIB_BODY.encode())
    assert "Calc" in changed_names(p, LIB_V1.encode(), LIB_SIG.encode())


def test_read_set_adds_csharp_importers_only_when_the_signature_changed():
    files = {"s/p/A.cs": LIB_SIG.encode(), "s/q/B.cs": USE.encode()}
    graph = imports.file_graph(sorted(files), files)
    _c, _i, importers = read_set(["s/p/A.cs"], graph, ["s/p/A.cs"])
    assert importers == ["s/q/B.cs"]
    _c, _i, importers = read_set(["s/p/A.cs"], graph, [])
    assert importers == []


def test_measure_and_replay_agree_on_a_csharp_signature_change(tmp_path):
    repo, _base = fixture.make_repo(tmp_path / "r", {"s/p/A.cs": LIB_V1, "s/q/B.cs": USE,
                                                     "s/q/Other.cs": "namespace Q { public class Other {} }\n"})
    fixture.commit(repo, {"s/p/A.cs": LIB_SIG})
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    parent = fixture.git(repo, "rev-parse", "HEAD^").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "s/p/A.cs",
                     "--out", str(pack)]) == Exit.OK
    name, count = metrics.counter()
    doc = metrics.write_metrics(repo, parent, head, 1, pack.read_text(), 12000, count, name)
    one = replay_mod.replay(repo, last=1, budget=12000, max_files=200, count=count,
                            tokenizer=name).to_obj()["commits"][0]
    assert doc["without_rco"]["tokens"] == one["without"]["tokens"]
    assert doc["without_rco"]["importer_files"] == one["without"]["importer_files"] == 1
    assert one["coverage"]["call_sites"] >= 1


def test_csharp_test_files_are_tests_for_affected():
    for p in ("Src/Json.Tests/A.cs", "Src/X/ThingTests.cs", "Src/X/ThingTest.cs", "tests/a/B.cs"):
        assert is_test(p), p
    assert not is_test("Src/Json/Thing.cs")
