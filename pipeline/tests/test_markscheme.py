"""Label normalisation and mark-scheme matching.

Pure logic, no network. These are the functions that decide whether a student
sees the right mark scheme next to a question, so they are the ones worth
pinning down with tests.
"""

from noteacademy_pipeline.markscheme import (
    _root_ordinal,
    match_mark_scheme,
    match_mcq_answers,
    normalise_label,
    parse_structured_mark_scheme,
)
from noteacademy_pipeline.schemas import MarkSchemeEntry


def _page(*lines: str) -> str:
    """One mark-scheme page's text layer: boilerplate header, then content.

    `parse_structured_mark_scheme` drops everything up to and including the
    copyright line on every page — a page with no such line is dropped
    whole, so every fixture page needs one even though the test never reads
    it back.
    """
    header = ["2", "Mark Scheme", "Syllabus", "Paper", "5054", "21", "© UCLES 2015"]
    return "\n".join(header + list(lines))


class TestNormaliseLabel:
    def test_accepts_the_spacing_variations_caie_actually_prints(self):
        for raw in ["7(a)(ii)", "7 (a) (ii)", "7(a)(II)", " 7 ( a ) ( ii ) "]:
            assert normalise_label(raw) == "7(a)(ii)"

    def test_keeps_bare_numbers_and_single_parts(self):
        assert normalise_label("12") == "12"
        assert normalise_label("3(b)") == "3(b)"

    def test_rejects_labels_with_no_question_number(self):
        assert normalise_label("(a)") is None
        assert normalise_label("BLANK PAGE") is None

    def test_does_not_confuse_roman_numerals_with_each_other(self):
        assert normalise_label("1(a)(i)") != normalise_label("1(a)(ii)")
        assert normalise_label("1(a)(iv)") == "1(a)(iv)"


class TestMatchMarkScheme:
    def test_matches_across_formatting_differences(self):
        result = match_mark_scheme(
            ["3(b)", "3(c)(i)"],
            [
                MarkSchemeEntry(display_label="3 (b)", marks=2, content="B1 for ..."),
                MarkSchemeEntry(display_label="3(c)(I)", marks=1, content="M1 for ..."),
            ],
        )
        assert result.is_clean
        assert result.matched["3(b)"].content == "B1 for ..."

    def test_reports_a_question_with_no_mark_scheme_rather_than_guessing(self):
        result = match_mark_scheme(
            ["5(a)", "5(b)"],
            [MarkSchemeEntry(display_label="5(a)", content="A1")],
        )
        assert result.matched.keys() == {"5(a)"}
        assert result.unmatched_questions == ["5(b)"]
        assert not result.is_clean

    def test_refuses_to_choose_between_duplicate_entries(self):
        # Two entries claiming one question means the paper uses a shape this
        # parser does not model. Attaching either one would be a coin flip.
        result = match_mark_scheme(
            ["4"],
            [
                MarkSchemeEntry(display_label="4", content="first"),
                MarkSchemeEntry(display_label="4", content="second"),
            ],
        )
        assert "4" not in result.matched
        assert not result.is_clean

    def test_surfaces_orphan_entries(self):
        result = match_mark_scheme(
            ["1"],
            [
                MarkSchemeEntry(display_label="1", content="ok"),
                MarkSchemeEntry(display_label="2", content="orphan"),
            ],
        )
        assert result.matched.keys() == {"1"}
        assert result.unmatched_entries == ["2"]


class TestParseStructuredMarkScheme:
    def test_reads_question_part_and_subpart_labels_with_their_marks(self):
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "1",
                    "(a) distance = 5.0 m",
                    "B1",
                    "(b) (i) 12 s",
                    "M1",
                    "(ii) speed = 2.0 m / s",
                    "A1",
                )
            ]
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["1(a)"].marks == 1
        assert by_label["1(a)"].content == "distance = 5.0 m"
        assert by_label["1(b)(i)"].marks == 1
        assert by_label["1(b)(ii)"].marks == 1

    def test_accumulates_more_than_one_mark_code_for_one_point(self):
        entries = parse_structured_mark_scheme(
            [_page("2", "(a) two reasons given", "C1", "A1")]
        )
        assert entries[0].marks == 2

    def test_reads_a_question_and_its_first_part_merged_on_one_line(self):
        # CAIE sometimes opens a question straight into its first part on one
        # printed line, exactly as the question paper itself does.
        entries = parse_structured_mark_scheme(
            [_page("10 (a) molecules not arranged regularly", "B1")],
            known_questions={"10"},
        )
        assert entries[0].display_label == "10(a)"

    def test_does_not_advance_on_a_bare_digit_inside_an_enumerated_point(self):
        # A marking point can itself be a numbered list ("1 speed and
        # direction ... 2 direction changes ..."), and a bare "2" there is
        # not a new question — ordinary ascending-order text a normal reader
        # would never mistake for one either.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "9",
                    "(a) (i) 1 speed and direction",
                    "2 direction changes",
                    "B1",
                )
            ],
            known_questions={"9"},
        )
        assert [e.display_label for e in entries] == ["9(a)(i)"]
        assert "2 direction changes" in entries[0].content

    def test_a_thousands_separated_value_matching_a_later_question_number_is_not_a_new_question(
        self,
    ):
        # CAIE renders "11 250 J" for 11250 J — a space-grouped thousands
        # value that happens to start with a real, later, known question
        # number. A bare question-number token is only ever alone on its
        # line or immediately followed by the part that opens it; "11 250 J"
        # is neither, so it must stay content for whatever question is
        # already open, not trigger a false start of question 11.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "10",
                    "(c) (E =) mL or 5 x 2250",
                    "C1",
                    "11 250 J",
                    "A1",
                    "11 (a) (i) 51",
                    "B1",
                )
            ],
            known_questions={"10", "11"},
        )
        by_label = {e.display_label: e for e in entries}
        assert "11 250 J" in by_label["10(c)"].content
        assert by_label["11(a)(i)"].content == "51"

    def test_reads_the_glued_table_style_with_no_space_in_the_label(self):
        # The "Question / Answer / Marks" table style (CAIE structured mark
        # schemes from roughly 2017 on) prints a label as one run with no
        # space at all — "1(a)", "1(b)(i)" — never split across tokens the
        # way the older prose style's own merges are.
        entries = parse_structured_mark_scheme(
            [_page("Question", "Answer", "Marks", "1(a)", "5.0 m", "B1", "1(b)(i)", "12 s", "M1")],
            known_questions={"1"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["1(a)"].content == "5.0 m"
        assert by_label["1(b)(i)"].content == "12 s"

    def test_a_repeated_question_number_on_every_row_does_not_reopen_it(self):
        # The table style reprints the current question's own number on
        # every one of its rows, not only when a new question opens — so
        # "1(c)" after "1(b)(i)"/"1(b)(ii)" must not be read as an attempt
        # to reopen question 1 (which the ascending check would reject
        # outright, leaving the whole row as unlabelled content instead).
        entries = parse_structured_mark_scheme(
            [_page("1(a)", "5.0 m", "B1", "1(b)", "6.0 m", "B1", "1(c)", "7.0 m", "B1")],
            known_questions={"1"},
        )
        assert {e.display_label for e in entries} == {"1(a)", "1(b)", "1(c)"}
        assert entries[-1].content == "7.0 m"

    def test_a_repeated_subpart_glued_to_its_next_point_is_not_a_new_entry(self):
        # "10(b)(ii)1" and "10(b)(ii)2" are sub-part (ii)'s first and second
        # marking points, glued straight onto its own repeated label with no
        # space anywhere — not a fourth label component, and not two
        # separate sub-parts that both happen to be called "(ii)". The
        # digit split off the label stays part of the content, the same as
        # an already-unglued enumerated point does.
        entries = parse_structured_mark_scheme(
            [_page("10(a)", "x", "B1", "10(b)(i)", "y", "B1", "10(b)(ii)1", "first point", "B1", "10(b)(ii)2", "second point", "B1")],
            known_questions={"10"},
        )
        by_label = {e.display_label: e for e in entries}
        assert sum(1 for e in entries if e.display_label == "10(b)(ii)") == 1
        assert by_label["10(b)(ii)"].content == "1 first point 2 second point"

    def test_strips_the_repeated_table_header_and_page_furniture(self):
        # The column header and page footer reprint before every top-level
        # question's own block, not only once per physical page — left in,
        # they read as trailing content tacked onto whatever entry was open
        # when one of them appeared.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "Question", "Answer", "Marks",
                    "1(a)", "first answer", "B1",
                    "Page 2 of 8", "Question", "Answer", "Marks",
                    "1(b)", "second answer", "B1",
                    "Section B",
                    "2(a)", "third answer", "B1",
                )
            ],
            known_questions={"1", "2"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["1(a)"].content == "first answer"
        assert by_label["1(b)"].content == "second answer"
        assert by_label["2(a)"].content == "third answer"

    def test_skips_a_generic_marking_principles_preamble(self):
        # "Science-Specific Marking Principles" opens with its own bare
        # numbered list before the real per-question table — indistinguishable
        # from a real question number by shape, ascending order, or even
        # known-questions membership alone if the preamble happens to run as
        # far as a real question number. The first "Question"/"Answer"/
        # "Marks" table header is what actually marks where the real table
        # begins, so everything before it is dropped rather than parsed.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "Science-Specific Marking Principles",
                    "1", "keywords should be read in context",
                    "2", "contradictory statements are not credited",
                    "Question", "Answer", "Marks",
                    "1(a)", "5.0 m", "B1",
                )
            ],
            known_questions={"1", "2"},
        )
        assert [e.display_label for e in entries] == ["1(a)"]

    def test_a_fifth_subpart_is_not_read_as_a_new_part_called_v(self):
        # "(i)", "(v)" and "(x)" are both a single-letter part shape and a
        # roman numeral. Once a part is already open — here, (b) — a bare
        # "(v)" is its fifth sub-part, not an attempt to open a part
        # literally called "(v)"; CAIE's own part-lettering never reaches
        # that far. Read as a new part, it would close out (b) mid-way and
        # misfile this point (and anything after it) under a part that does
        # not exist in the question at all.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "10(a)", "x", "B1",
                    "10(b)(i)", "first", "B1",
                    "10(b)(ii)", "second", "B1",
                    "10(b)(iii)", "third", "B1",
                    "10(b)(iv)", "fourth", "B1",
                    "10(b)(v)", "fifth", "A1",
                )
            ],
            known_questions={"10"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["10(b)(v)"].content == "fifth"
        assert "10(v)" not in by_label

    def test_an_uppercase_component_reference_is_not_a_part_label(self):
        # A mark scheme routinely names a labelled point on a circuit or
        # ray diagram in its own marking text — "(Z) has the same
        # potential difference" — and CAIE always prints a real part or
        # sub-part label lowercase. Matched case-insensitively, "(Z)" would
        # close out the real part (b) and misfile the point after it under
        # a part "(z)" that does not exist in the question at all.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "8(b)(i)", "first", "B1",
                    "8(b)(ii)", "(Z) has the same reading", "B1",
                )
            ],
            known_questions={"8"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["8(b)(ii)"].content == "(Z) has the same reading"
        assert "8(z)" not in by_label

    def test_a_stray_space_inside_a_label_does_not_hide_it(self):
        # A PDF's text layer occasionally renders a label's own glyph
        # spacing as "(i )" rather than "(i)" — a rendering artefact of
        # that particular print run, not a real character — which fails
        # the exact match a sub-part label otherwise needs.
        entries = parse_structured_mark_scheme(
            [_page("9", "(b) (i ) 700 N", "B1")],
            known_questions={"9"},
        )
        assert entries[0].display_label == "9(b)(i)"
        assert entries[0].content == "700 N"

    def test_a_doubled_opening_paren_does_not_hide_a_label(self):
        # Another PDF-specific rendering glitch: "1(a)((i)" for "1(a)(i)",
        # an extra open paren stuck to the front with nothing between them.
        # The extra "(" breaks the clean "(x)" shape every label pattern
        # needs, and unlike the merge cases above there is no real content
        # to preserve by leaving it alone.
        entries = parse_structured_mark_scheme(
            [_page("1(a)((i)", "1.8 m / s2", "A1")],
            known_questions={"1"},
        )
        assert entries[0].display_label == "1(a)(i)"
        assert entries[0].content == "1.8 m / s2"

    def test_a_nuclide_mass_number_is_not_a_new_question(self):
        # Nuclide notation renders as a bare mass number on its own line,
        # with the atomic number and element symbol glued together on the
        # line right after it — "9" then "4Be" for beryllium-9. A bare
        # number alone on its own line otherwise satisfies every check a
        # genuine question start has (ascending, known, nothing else on
        # the line), so a mass number matching a real, later question's own
        # number would open it early — corrupting every label from there
        # until the real one is reached and fails its own ascending check
        # in turn, exactly the "11 250 J" case above one level up.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "5(a)(i)", "2 more protons than electrons", "B1",
                    "5(a)(ii)", "9", "4Be", "B1",
                    "5(b)", "a form of an element", "B1",
                )
            ],
            known_questions={"5", "9"},
        )
        by_label = {e.display_label: e for e in entries}
        assert "5(a)(ii)" in by_label
        assert by_label["5(a)(ii)"].content == "9 4Be"
        assert "9" not in by_label
        assert by_label["5(b)"].content == "a form of an element"


class TestStructuredAlternativeQuestions:
    """CAIE's "EITHER ... OR ..." split (see segment_structured.py's
    _ALTERNATIVE_LABELS) reads the same word two different ways: a real
    structural split, or simply offering an alternative wording for one
    already-open marking point. Only `known_questions` — the question
    paper's own labels — tells the two apart."""

    def test_whole_question_branch_matches_the_question_papers_own_shape(self):
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "8",
                    "EITHER",
                    "(a) steel",
                    "B1",
                    "(b) vertical",
                    "B1",
                    "OR",
                    "(a) NOT gate",
                    "B1",
                )
            ],
            known_questions={"8", "8(either)", "8(either)(a)", "8(either)(b)", "8(or)", "8(or)(a)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label.keys() == {"8(either)(a)", "8(either)(b)", "8(or)(a)"}
        assert by_label["8(or)(a)"].content == "NOT gate"

    def test_mid_part_branch_nests_inside_a_part_the_question_paper_already_has(self):
        # The table style never restates "(b)" before the split opens — only
        # the part's own later row does — so the split's position can only
        # be worked out once that row names it, checked against what the
        # question paper itself already has.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "8(a)(i)", "7.5 V", "A1",
                    "8(a)(ii)", "resistance falls", "B1",
                    "EITHER",
                    "8(b)", "coil becomes magnetised", "B1",
                    "OR",
                    "8(b)", "transistor switches on", "B1",
                )
            ],
            known_questions={
                "8", "8(a)", "8(a)(i)", "8(a)(ii)", "8(b)",
                "8(b)(either)", "8(b)(or)",
            },
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label.keys() >= {"8(b)(either)", "8(b)(or)"}
        assert by_label["8(b)(either)"].content == "coil becomes magnetised"
        assert by_label["8(b)(or)"].content == "transistor switches on"

    def test_or_resolves_immediately_when_either_never_names_a_part(self):
        # Both sides are pure prose, no part or sub-part label of their own —
        # the split must be answering whatever was already open ("(b)"),
        # worked out the moment "OR" arrives rather than waiting further.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "7(a)", "current = 0.25 A", "C1",
                    "(b) EITHER",
                    "capacitor stores charge", "B1",
                    "OR",
                    "current into transistor", "B1",
                )
            ],
            known_questions={"7", "7(a)", "7(b)", "7(b)(either)", "7(b)(or)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["7(b)(either)"].content == "capacitor stores charge"
        assert by_label["7(b)(or)"].content == "current into transistor"

    def test_glued_root_and_branch_letter_notation(self):
        # A third way the table style marks the split: folded into the row's
        # own repeated label, no separator at all — "5E(a)".
        entries = parse_structured_mark_scheme(
            [_page("5E(a)", "capacitor stores charge", "B1", "5O(a)", "NOT gate", "B1")],
            known_questions={"5", "5(either)", "5(either)(a)", "5(or)", "5(or)(a)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label["5(either)(a)"].content == "capacitor stores charge"
        assert by_label["5(or)(a)"].content == "NOT gate"

    def test_a_glued_scientific_notation_value_is_not_read_as_a_split(self):
        # "5E10" (5x10^10) has the same shape as a genuine glued branch up to
        # the letter — the lookahead after it is what tells them apart.
        entries = parse_structured_mark_scheme(
            [_page("9", "(a) 5E10", "A1")],
            known_questions={"9", "9(a)"},
        )
        assert entries[0].content == "5E10"

    def test_content_or_is_not_mistaken_for_a_structural_split(self):
        # An alternative acceptable value for the *same* marking point, not
        # a structural split — the question paper never split this question,
        # so "OR" here is read as ordinary content.
        entries = parse_structured_mark_scheme(
            [_page("9", "(a) Xe", "131", "54", "OR", "Xe", "131", "54", "B1")],
            known_questions={"9", "9(a)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label.keys() == {"9(a)"}
        assert "OR" in by_label["9(a)"].content

    def test_a_genuine_fifth_part_does_not_look_like_a_branch(self):
        # A question's own real "(e)" part must not make this parser treat
        # an unrelated "OR" anywhere else in the same question as a split.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "10(c)", "some value OR another value", "B1",
                    "10(d)(i)", "answer one", "B1",
                    "10(e)", "final answer", "B1",
                )
            ],
            known_questions={"10", "10(c)", "10(d)", "10(d)(i)", "10(e)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label.keys() == {"10(c)", "10(d)(i)", "10(e)"}


class TestLetteredQuestionNumbers:
    """Chemistry numbers its two sections as one continuing sequence rather
    than restarting each one — "A1".."A5" then "B6".."B9" — unlike a plain
    "1".."9". See segment_structured.py's own `_LEVEL_PATTERNS` for the
    question-paper side of the same convention."""

    def test_root_ordinal_reads_past_the_section_letter(self):
        assert _root_ordinal("7") == 7
        assert _root_ordinal("A1") == 1
        assert _root_ordinal("B6") == 6

    def test_a_lettered_question_opens_normally(self):
        entries = parse_structured_mark_scheme(
            [_page("A1 (a) Nickel / Ni", "B1")],
            known_questions={"A1", "A1(a)"},
        )
        assert entries[0].display_label == "A1(a)"

    def test_a_second_section_continues_the_same_numbering(self):
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "A1", "(a) Nickel", "B1",
                    "B2", "(a) Sulfuric acid is a strong acid", "B1",
                )
            ],
            known_questions={"A1", "A1(a)", "B2", "B2(a)"},
        )
        by_label = {e.display_label: e for e in entries}
        assert by_label.keys() == {"A1(a)", "B2(a)"}

    def test_a_guidance_note_before_any_part_still_opens_the_question(self):
        # A section-lettered question sometimes opens with a guidance note
        # of its own before any part does, with nothing part-shaped
        # anywhere on the same line — unlike a plain-numbered question,
        # this is not mistaken for content the way "11 250 J" would be,
        # since a measurement is never written with a letter prefix.
        entries = parse_structured_mark_scheme(
            [
                _page(
                    "A1 Allow correct name but formula takes precedence",
                    "(a) V2O5",
                    "B1",
                )
            ],
            known_questions={"A1", "A1(a)"},
        )
        by_label = {e.display_label: e for e in entries}
        # The guidance note itself becomes its own entry, attached to the
        # question as a whole rather than any one part — an orphan once
        # matched against the question paper's own leaves, since "A1" is a
        # container there, but real: a marker really did write it down.
        assert by_label["A1"].content == "Allow correct name but formula takes precedence"
        assert by_label["A1(a)"].content == "V2O5"


class TestMatchMcqAnswers:
    def test_attaches_answers_by_question_number(self):
        matched, unmatched = match_mcq_answers(
            ["1", "2", "3"], {"1": "C", "2": "A", "3": "D"}
        )
        assert matched == {"1": "C", "2": "A", "3": "D"}
        assert unmatched == []

    def test_reports_questions_the_grid_does_not_cover(self):
        matched, unmatched = match_mcq_answers(["1", "2"], {"1": "B"})
        assert matched == {"1": "B"}
        assert unmatched == ["2"]
