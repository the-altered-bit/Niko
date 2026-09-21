"""Source-aware diagnostics for Niko 2.

A Diagnostic carries a message plus an optional source position:
line, col, and end_col (for a caret span). Passes that only know the
offending *text* but not its position -- e.g. the typechecker, which
works on the AST without the source -- can pass ``token`` instead: the
formatter resolves it against the source line so the caret still lands
on the right place.

Rendering (``format_diagnostic``)::

    Niko error in prog.niko: line 1, column 5
      say totl(42)
          ^^^^
    unknown name "totl". Did you mean "text"?
"""

class Diagnostic(Exception):
    def __init__(self, message, line=None, col=None, end_col=None, token=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.end_col = end_col
        self.token = token


def _resolve_span(exc, line_text):
    """Return (col, width) or (None, None) for the caret."""
    col, width = exc.col, None
    if exc.end_col is not None and col is not None:
        width = max(1, exc.end_col - col)
    if col is None and exc.token:
        idx = line_text.find(exc.token)
        if idx != -1:
            col = idx + 1
            width = len(exc.token)
    if col is None:
        return None, None
    col = max(1, min(col, len(line_text) + 1))
    return col, width or 1


def format_diagnostic(source, exc, path='<memory>'):
    header = f'Niko error in {path}'
    msg = getattr(exc, 'message', None) or str(exc)
    line = getattr(exc, 'line', None)
    lines = source.splitlines() if source else []
    if line is not None and 1 <= line <= len(lines):
        line_text = lines[line - 1]
        col, width = _resolve_span(exc, line_text)
        if col is not None:
            caret = ' ' * (col - 1) + '^' * width
            return (f'{header}: line {line}, column {col}\n'
                    f'  {line_text}\n'
                    f'  {caret}\n'
                    f'{msg}')
        return (f'{header}: line {line}\n'
                f'  {line_text}\n'
                f'{msg}')
    return f'{header}: {msg}'
