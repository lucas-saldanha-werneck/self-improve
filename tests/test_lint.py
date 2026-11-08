import json
import pytest
from conftest import pyeval

WORDS = ["yesterday", "last week", "last month", "next week", "ontem", "semana passada"]


def rel_dates(env, pages):
    for name, body in pages.items():
        (env["wiki"] / name).write_text(body)
    out = pyeval(env, "import json, improve_system as m; "
                      "print(json.dumps([i['detail'] for i in m.lint({}) if i['kind'] == 'relative-date']))")
    return json.loads(out)


@pytest.mark.parametrize("w", WORDS)
@pytest.mark.parametrize("fmt", ['the batch expired "{w}", 07/08.',
                                 "the batch expired “{w}”, 07/08.",
                                 "the batch expired `{w}`, 07/08.",
                                 "> she said it was {w} indeed."])
def test_relative_date_inside_quote_ignored(env, w, fmt):
    assert rel_dates(env, {"p.md": "# P\n\n" + fmt.format(w=w) + "\n"}) == []


@pytest.mark.parametrize("w", WORDS)
def test_relative_date_plain_text_still_flags(env, w):
    assert rel_dates(env, {"p.md": f"# P\n\nThis changed {w}, says the source.\n"}) == [f"relative date '{w}'"]


def test_relative_date_quote_does_not_hide_plain_on_same_line(env):
    body = '# P\n\nThe batch expired "yesterday" and the price fell last week.\n'
    assert rel_dates(env, {"p.md": body}) == ["relative date 'last week'"]


def test_broken_link_and_index(env):
    (env["wiki"] / "INDEX.md").write_text("# Index\n\n- [[a]]\n")
    (env["wiki"] / "a.md").write_text("# A\n\nSee [[missing-page]] and [[b]].\n")
    (env["wiki"] / "b.md").write_text("# B\n\nBack to [[a]].\n")
    out = json.loads(pyeval(env, "import json, improve_system as m; print(json.dumps(m.lint({})))"))
    kinds = {(i["kind"], i["detail"]) for i in out}
    assert ("broken-link", "[[missing-page]] does not exist") in kinds
    assert ("index-entry", "b.md missing from INDEX") in kinds


def test_no_wiki_configured_skips_wiki_lint(env):
    env["env"].pop("SELF_IMPROVE_WIKI_DIR")
    (env["wiki"] / "a.md").write_text("# A\n\n[[missing]] yesterday\n")
    out = json.loads(pyeval(env, "import json, improve_system as m; print(json.dumps(m.lint({})))"))
    assert not [i for i in out if i["kind"] in ("broken-link", "relative-date", "index-entry")]


def test_memory_index_lint(env):
    mem = env["claude"] / "projects" / "-some-project" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [Gone](gone.md) - old\n- [Kept](kept.md) - ok\n")
    (mem / "kept.md").write_text("x")
    (mem / "orphan.md").write_text("x")
    out = json.loads(pyeval(env, "import json, improve_system as m; print(json.dumps(m.lint({})))"))
    details = sorted(i["detail"] for i in out if i["kind"] == "memory-index")
    assert details == ["orphan.md has no line in the index", "points to gone.md, which does not exist"]
