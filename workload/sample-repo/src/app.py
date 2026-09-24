"""String helpers used by the sandbox demo workload."""


def title_case(text: str) -> str:
    """Return text with each word capitalised."""
    return " ".join(word.capitalize() for word in text.split())
