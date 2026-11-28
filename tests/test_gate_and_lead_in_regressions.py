"""Whole-token citation gate, and trailing list notes attaching to the right parent."""
import pytest

from reg_intel.extract import find_lead_in, verify_citation

SOURCE = "The limit is Rs 15,230.75 and banks shall report within 30 days."


@pytest.mark.parametrize("span", ["Rs 1", "Rs 15", "230.75", "hall report", "within 3"])
def test_partial_words_and_numbers_are_rejected(span):
    assert not verify_citation(span, SOURCE)


@pytest.mark.parametrize("span", ["Rs 15,230.75", "banks shall report within 30 days."])
def test_whole_quotes_are_accepted(span):
    assert verify_citation(span, SOURCE)


NESTED = [
    "The insurer shall comply with the following:",
    "(a) Premium may be paid from abroad provided that:-",
    "v) the policy holder satisfies the bank that:-",
    "(1) the funds were remitted through normal channels",
    "(b) Claims may be settled in the currency of the policy",
]


def test_trailing_note_skips_the_list_nested_in_its_previous_sibling():
    lead = find_lead_in(NESTED, 4)
    assert "v) the policy holder" not in lead
    assert lead == "The insurer shall comply with the following:"


def test_second_item_of_a_nested_list_keeps_its_own_parent():
    lines = ["A bank must ensure that:", "(a) the form is signed;", "(b) the copy is kept"]
    assert find_lead_in(lines, 2) == "A bank must ensure that:"


def test_item_whose_previous_sibling_is_nowhere_above_keeps_its_parent():
    lines = ["Every insurer shall ensure that:", "(b) the copy is kept"]
    assert find_lead_in(lines, 1) == "Every insurer shall ensure that:"
