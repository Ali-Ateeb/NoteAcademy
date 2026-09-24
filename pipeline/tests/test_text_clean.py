"""Control characters out of extracted text.

Every string here is copied from a real row: the barcode strip glued to the
end of a question, and the symbol-font glyph in a mark scheme.
"""

# ruff: noqa: E501  -- the strings are copied from real rows; wrapping them would obscure that.
import pytest

from noteacademy_pipeline.text_clean import clean_extracted_text, has_control_characters


class TestHeaderStrips:
    def test_the_barcode_strip_and_its_page_number_are_removed_from_the_end(self):
        text = "e formula of calcium carbide.  [1] [Total: 9] 11 ,\x01\x01\x01\x01 \x01\x01\x01\x01\x01\x01\x02\x02,"
        assert clean_extracted_text(text) == "e formula of calcium carbide. [1] [Total: 9]"

    def test_a_strip_with_a_space_before_its_closing_comma(self):
        text = "used in electrolysis. 1  2  [2] 8 ,\x01\x01\x01\x01 \x01\x01\x01\x01\x01\x01\x01 ,"
        assert clean_extracted_text(text) == "used in electrolysis. 1 2 [2]"

    def test_a_blank_page_between_two_strips_goes_with_them(self):
        text = (
            "(b)(i).    [1] [Total: 8] 5 BLANK PAGE ,\x01\x01\x01\x01 "
            "\x01\x01\x01\x01\x01\x01\x01\x06, 6 ,\x01\x01\x01\x01 \x01\x01\x01\x01\x01\x01\x01\x07,"
        )
        assert clean_extracted_text(text) == "(b)(i). [1] [Total: 8]"

    def test_a_number_that_is_not_in_front_of_a_strip_is_kept(self):
        assert clean_extracted_text("Total 11 \x02 and 12 marks") == "Total 11 and 12 marks"


class TestSymbols:
    def test_the_arrow_between_two_formulas_becomes_an_arrow_in_a_mark_scheme(self):
        assert (
            clean_extracted_text("2H2O2 \x01 2H2O + O2 / ALLOW any correct mult", symbols=True)
            == "2H2O2 → 2H2O + O2 / ALLOW any correct mult"
        )

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Cl2 + 2I– \x01 I2 + 2Cl – Ignore", "Cl2 + 2I– → I2 + 2Cl – Ignore"),
            ("Cu2+(aq)  +  CO3 2–(aq)  \x01  CuCO3 (s)", "Cu2+(aq) + CO3 2–(aq) → CuCO3 (s)"),
            ("CuCO3.Cu(OH) 2 + C \x01 2Cu + 2CO2", "CuCO3.Cu(OH) 2 + C → 2Cu + 2CO2"),
            ("HCl \x01 H+ + Cl – (1)", "HCl → H+ + Cl – (1)"),
        ],
    )
    def test_real_equations(self, text, expected):
        assert clean_extracted_text(text, symbols=True) == expected

    def test_a_tick_that_leads_a_line_stays_a_tick(self):
        assert (
            clean_extracted_text("\x01 diffusion into the alveoli (box 2) ; \x01 the diaphragm relaxes (box 4) ;", symbols=True)
            == "✓ diffusion into the alveoli (box 2) ; ✓ the diaphragm relaxes (box 4) ;"
        )

    def test_ticks_jammed_against_an_x_stay_ticks(self):
        assert (
            clean_extracted_text("(at AB) xx\x01 \x01xx xx\x01 \x01\x01\x01 \x01xx", symbols=True)
            == "(at AB) xx✓ ✓xx xx✓ ✓✓✓ ✓xx"
        )

    def test_without_symbols_the_glyph_is_dropped_not_guessed_at(self):
        # A diagram label in question text: no way to know what it drew.
        assert clean_extracted_text("output \x02 [2]") == "output [2]"


class TestUntouched:
    @pytest.mark.parametrize("text", [None, "", "plain text", "Two  spaces stay\nand so do\ttabs."])
    def test_text_without_control_characters_is_returned_as_it_was(self, text):
        assert clean_extracted_text(text) == text
        assert clean_extracted_text(text, symbols=True) == text

    def test_a_nul_byte_is_removed(self):
        assert clean_extracted_text("a\x00b") == "ab"

    def test_has_control_characters(self):
        assert has_control_characters("a\x01b")
        assert not has_control_characters("a\tb\nc")
        assert not has_control_characters(None)
