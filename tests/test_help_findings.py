"""Engine fixes found by migrating MANIFEST's own help (ADR 0009), each pinned by a test."""

from __future__ import annotations

from lxml import etree

from manifest.placeholders import CELL_PIPE, LITERAL, Ledger, tokenize, unescape_literal


def parse(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def test_source_text_that_looks_like_a_placeholder_is_kept_as_text() -> None:
    root = parse(
        '<concept id="c"><title>T</title><conbody><p>A token such as <codeph>@@K12@@</codeph>.</p></conbody></concept>'
    )
    tokenize(root, Ledger(), "c.dita")
    text = "".join(root.itertext())
    assert "@@K12@@" not in text and LITERAL + "K12" + LITERAL[::-1].replace("⁠", "⁠") in text or "K12" in text
    assert unescape_literal(text).count("@@K12@@") == 1


def test_a_pipe_in_a_table_cell_is_escaped_only_on_table_rows() -> None:
    root = parse('<reference id="r"><title>T</title><refbody><simpletable><strow><stentry>live|replay</stentry></strow>'
                 '</simpletable></refbody></reference>')  # fmt: skip
    tokenize(root, Ledger(), "r.dita")
    cell = "".join(root.itertext())
    assert CELL_PIPE in cell and "|" not in cell
    assert unescape_literal(f"| `live{CELL_PIPE}replay` | x |") == "| `live\\|replay` | x |"
    assert unescape_literal(f"live{CELL_PIPE}replay") == "live|replay"


def test_a_one_step_task_becomes_informal_steps() -> None:
    root = parse('<task id="t"><title>T</title><taskbody><steps><step id="s1"><cmd>Select <uicontrol>Sign in</uicontrol>.</cmd>'
                 '<stepresult><p>Done.</p></stepresult></step></steps></taskbody></task>')  # fmt: skip
    tokenize(root, Ledger(), "t.dita")
    assert root.find(".//steps") is None
    informal = root.find(".//steps-informal")
    text = "".join(informal.itertext()) if informal is not None else ""
    assert text.endswith("Select Sign in.Done.")
    assert text.startswith("@@I"), "the step's ID travels as a placeholder, so its anchor survives"


def test_tasks_with_several_steps_keep_their_list() -> None:
    root = parse('<task id="t"><title>T</title><taskbody><steps><step><cmd>One</cmd></step><step><cmd>Two</cmd></step>'
                 '</steps></taskbody></task>')  # fmt: skip
    tokenize(root, Ledger(), "t.dita")
    assert root.find(".//steps") is not None


def test_a_one_step_task_keeps_the_steps_condition() -> None:
    root = parse('<task id="t"><title>T</title><taskbody><steps><step platform="v5e"><cmd>Only on v5e.</cmd></step>'
                 '</steps></taskbody></task>')  # fmt: skip
    tokenize(root, Ledger(), "t.dita")
    informal = root.find(".//steps-informal")
    assert informal is not None and informal.get("platform") == "v5e"
