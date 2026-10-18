"""Token-Schaetzung. Anthropics Tokenizer ist nicht lokal verfuegbar -- cl100k
dient als Naeherung. Zahlen daher ueberall als '~' ausweisen."""
import json

try:
    import tiktoken
    # o200k_base (GPT-4o) liegt bei Text und Code naeher an aktuellem Claude
    # als cl100k. Exakt trifft es Anthropic nicht -- daher bleiben Zahlen '~'.
    _enc = tiktoken.get_encoding("o200k_base")
except Exception:
    _enc = None


def count(text) -> int:
    if text is None:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    if _enc is not None:
        return len(_enc.encode(text, disallowed_special=()))
    return len(text) // 4
