"""Enable `python -m arxivist ...` as an alternative to the `arxivist` script."""

from .cli import app

if __name__ == "__main__":
    app()
