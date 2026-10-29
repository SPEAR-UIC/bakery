"""bakery/tests/test_plain_text.py - table sizing and the pandoc rendering

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import pytest

from plain_text import TEXT_COLUMNS, has_pandoc, render_text, sized_tables

TABLE = ("| Field | Type | Meaning |\n|---|---|---|\n"
         "| `rack` | Text | " + "a long meaning " * 12 + "|\n\nAfter.")


def test_narrow_columns_keep_their_width_and_the_widest_fills_the_rest():
    separator = sized_tables(TABLE).split("\n")[1]
    assert [len(part) for part in separator.strip("|").split("|")] == [6, 4, TEXT_COLUMNS - 2 * 3 - 6 - 4]


def test_text_outside_tables_is_unchanged():
    assert sized_tables("plain |---| words\n---\n") == "plain |---| words\n---\n"


@pytest.mark.skipif(not has_pandoc(), reason="pandoc is not installed")
def test_strings_pass_through_unchanged():
    text = render_text('He said "constant" for x_1 ~ $5 and $6, H~2~O, 2^10^, *.csv\n')
    assert text.strip() == 'He said "constant" for x_1 ~ $5 and $6, H~2~O, 2^10^, *.csv'


@pytest.mark.skipif(not has_pandoc(), reason="pandoc is not installed")
def test_long_meanings_wrap_within_the_page():
    assert max(map(len, render_text(TABLE).splitlines())) <= 100
    assert " ".join(render_text(TABLE).split()).count("a long meaning") == 12
