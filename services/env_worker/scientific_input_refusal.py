"""Explicit integration-authored input recovery, never inferred from arbitrary errors."""


class InputRefused(ValueError):
    """An integration could not open this input; accepted Project data is unchanged."""

    def __init__(self, reason, next_step):
        for text in (reason, next_step):
            if (type(text) is not str or not 0 < len(text) <= 1000 or text != text.strip()
                    or not text.isprintable()):
                raise ValueError('Input refusal needs bounded single-line user guidance')
        super().__init__(reason)
        self.next_step = next_step
