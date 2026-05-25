from pathlib import Path


def test_homebrew_formula_declares_python_resources() -> None:
    formula = Path("Formula/bonsai.rb").read_text(encoding="utf-8")

    for package in (
        "annotated-doc",
        "click",
        "markdown-it-py",
        "mdurl",
        "pygments",
        "rich",
        "shellingham",
        "typer",
    ):
        assert f'resource "{package}"' in formula
