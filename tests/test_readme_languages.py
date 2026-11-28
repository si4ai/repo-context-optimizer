"""The README names every supported language in its Code contracts list."""
from pathlib import Path

from rco.extract.langs import LANG_OF

README = Path(__file__).resolve().parents[1] / "README.md"
NAME = {"js": "JavaScript", "go": "Go", "rust": "Rust", "java": "Java", "kotlin": "Kotlin",
        "csharp": "C#", "php": "PHP", "sh": "shell"}


def _code_contracts() -> str:
    return next(ln for ln in README.read_text().splitlines() if ln.startswith("- **Code contracts:**"))


def test_every_language_has_a_readme_name():
    assert set(LANG_OF.values()) <= set(NAME), "a new language needs its README name here"


def test_every_supported_language_is_in_the_code_contracts_list():
    line = _code_contracts()
    for lang in sorted(set(LANG_OF.values())):
        assert NAME[lang] in line, f"{NAME[lang]} is missing from the README Code contracts list"


def test_each_class_language_says_what_public_means():
    line = _code_contracts().split("(or the language's equivalent:", 1)[1]
    for lang in ("java", "csharp", "kotlin", "php", "rust", "go"):
        assert NAME[lang] in line, f"{NAME[lang]} has no public rule in the Code contracts list"
